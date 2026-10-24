import csv
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.db import Database
from backend.worker import Collector
from backend.connectors import XConnector, ThreadsConnector, FacebookConnector
from backend.app import create_app

NOW = datetime.now(timezone.utc).isoformat()

def x_payload(cursor=None):
    return {'data': [{'id':'123', 'text':'AI marketing cần kiểm chứng', 'author_id':'u1',
                      'created_at': NOW, 'lang':'vi',
                      'public_metrics':{'like_count':5,'reply_count':2,'retweet_count':1}}],
            'meta': {'newest_id':'123', **({'next_token':cursor} if cursor else {})}}

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=self.temp.name+'/db.sqlite3', app_token='a'*32,
                                 x_token='secret-source-token', page_size=10, x_daily_read_cap=100)
        self.requests=[]
        self.reply=lambda request: httpx.Response(200,json=x_payload())
        def handler(request):
            self.requests.append(request)
            return self.reply(request)
        self.upstream=httpx.Client(transport=httpx.MockTransport(handler))
        self.app=create_app(self.settings,self.upstream)
        self.api=TestClient(self.app)
        self.headers={'Authorization':'Bearer '+self.settings.app_token}
        self.db=self.app.state.db

    def tearDown(self):
        self.api.close();self.upstream.close();self.temp.cleanup()

    def job(self, **kwargs):
        payload={'platform':'x','query':'AI marketing','interval_seconds':60,**kwargs}
        r=self.api.post('/api/jobs',json=payload,headers=self.headers)
        self.assertEqual(r.status_code,201)
        return r.json()['id']

    def ready(self, job):
        with self.db.connection() as db:
            db.execute('UPDATE jobs SET next_run=0 WHERE id=?',(job,))

    def run_job(self, job):
        return self.api.post(f'/api/jobs/{job}/run',headers=self.headers).json()

    def test_auth_and_missing_credentials(self):
        self.assertEqual(self.api.get('/api/posts').status_code,401)
        self.settings.x_token=''
        j=self.job()
        self.assertEqual(self.run_job(j)['status'],'missing_credentials')
        self.assertFalse(self.requests)
        self.assertEqual(self.api.get('/api/sources',headers=self.headers).json()['sources'][0]['access'],'unverified')

    def test_collect_dedup_filter_bookmark_export(self):
        j=self.job()
        self.assertEqual(self.run_job(j)['inserted'],1)
        self.ready(j)
        self.assertEqual(self.run_job(j)['inserted'],0)
        self.assertEqual(self.requests[-1].url.params['since_id'],'123')
        result=self.api.get('/api/posts?q="ai marketing"&min_engagement=8',headers=self.headers).json()
        self.assertEqual(result['total'],1)
        self.assertEqual(self.api.get('/api/posts?exclude=kiểm',headers=self.headers).json()['total'],0)
        pid=result['items'][0]['id']
        self.api.put(f'/api/posts/{pid}/bookmark',json={'saved':True},headers=self.headers)
        self.assertEqual(self.api.get('/api/posts?saved=true',headers=self.headers).json()['total'],1)
        report=self.api.get('/api/reports.csv?q=AI',headers=self.headers)
        self.assertIn('https://x.com/i/web/status/123',report.text)
        self.assertEqual(report.headers['x-report-truncated'],'false')
        with self.db.connection() as db:
            db.execute('UPDATE posts SET text=?',('  =HYPERLINK("evil")',))
        report=self.api.get('/api/reports.csv',headers=self.headers)
        self.assertTrue(list(csv.reader(io.StringIO(report.text.lstrip('\ufeff'))))[1][3].startswith("'"))

    def test_pagination_persists_across_restart(self):
        j=self.job();self.reply=lambda r:httpx.Response(200,json=x_payload('cursor-2'))
        self.run_job(j);self.ready(j)
        with self.db.connection() as db:
            self.assertIsNone(db.execute('SELECT since_id FROM jobs').fetchone()[0])
        collector=Collector(self.settings,Database(self.settings.db_path),self.upstream)
        self.reply=lambda r:httpx.Response(200,json={'data':[],'meta':{}})
        self.assertEqual(collector.run(j,manual=True)['status'],'success')
        self.assertEqual(self.requests[-1].url.params['next_token'],'cursor-2')
        with self.db.connection() as db:
            self.assertEqual(db.execute('SELECT since_id FROM jobs').fetchone()[0],'123')

    def test_429_platform_cooldown_and_no_secret_in_logs(self):
        j=self.job();other=self.job()
        self.reply=lambda r:httpx.Response(429,headers={'Retry-After':'300'},json={'error':self.settings.x_token})
        result=self.run_job(j)
        self.assertEqual(result['error_code'],'rate_limited')
        self.assertGreaterEqual(result['retry_after'],300)
        self.assertEqual(self.run_job(other)['status'],'deferred')
        self.assertEqual(len(self.requests),1)
        self.assertNotIn(self.settings.x_token,self.api.get('/api/runs',headers=self.headers).text)

    def test_auth_failure_pauses_schedule(self):
        j=self.job();self.api.patch(f'/api/jobs/{j}',json={'enabled':True},headers=self.headers)
        self.reply=lambda r:httpx.Response(403,json={'message':self.settings.x_token})
        self.assertTrue(self.run_job(j)['paused'])
        self.assertEqual(self.db.jobs()[0]['enabled'],0)

    def test_read_cap_is_atomic_across_jobs(self):
        self.settings.x_daily_read_cap=10
        a,b=self.job(),self.job()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda j:self.app.state.collector.run(j,manual=True),[a,b]))
        self.assertEqual(sorted(r['status'] for r in results),['daily_read_cap','success'])
        self.assertEqual(len(self.requests),1)

    def test_only_one_concurrent_claim(self):
        j=self.job()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.app.state.collector.run(j,manual=True),range(2)))
        self.assertEqual(sum(r['status']=='success' for r in results),1)
        self.assertEqual(len(self.requests),1)

    def test_retry_backoff_and_worker_schedule(self):
        j=self.job();self.assertEqual(self.app.state.collector.tick()['status'],'idle')
        self.api.patch(f'/api/jobs/{j}',json={'enabled':True},headers=self.headers)
        self.reply=lambda r:(_ for _ in ()).throw(httpx.ConnectError('secret URL token=hidden'))
        result=self.app.state.collector.tick()
        self.assertEqual(result['error_code'],'network_error')
        self.assertEqual(self.run_job(j)['status'],'deferred')
        self.assertNotIn('hidden',self.api.get('/api/runs',headers=self.headers).text)

    def test_meta_adapters_and_null_metrics(self):
        self.settings.threads_token='thread-secret'
        self.reply=lambda r:httpx.Response(200,json={'data':[{'id':'t1','text':'AI','timestamp':NOW,
            'username':'test','permalink':'https://www.threads.net/@test/post/1','media_type':'TEXT_POST'}],
            'paging':{'next':'https://untrusted.invalid/?access_token=secret','cursors':{'after':'safe-cursor'}}})
        j=self.job(platform='threads',query='AI');result=self.run_job(j)
        self.assertTrue(result['has_more'])
        post=self.api.get('/api/posts',headers=self.headers).json()['items'][0]
        self.assertIsNone(post['likes'])
        self.settings.facebook_token='page-secret';self.settings.graph_version='v99.0'
        self.reply=lambda r:httpx.Response(200,json={'data':[{'id':'p1','message':'No keyword here','created_time':NOW}]})
        j=self.job(platform='facebook',page_id='12345',query='marketing');self.run_job(j)
        self.assertEqual(self.requests[-1].url.host,'graph.facebook.com')
        self.assertEqual(self.api.get(f'/api/posts?job_id={j}',headers=self.headers).json()['total'],0)

    def test_bad_payload_and_report_limits(self):
        j=self.job();self.reply=lambda r:httpx.Response(200,json={'data':[{'id':'1','text':'AI'}]})
        self.assertEqual(self.run_job(j)['error_code'],'invalid_source_timestamp')
        self.assertEqual(self.api.get('/api/posts',headers=self.headers).json()['total'],0)
        self.assertEqual(self.api.get('/api/posts?min_engagement=-1',headers=self.headers).status_code,422)
        self.assertEqual(self.api.post('/api/jobs',headers=self.headers,json={'platform':'facebook','query':'x','page_id':'../me'}).status_code,422)

if __name__ == '__main__':
    unittest.main()
