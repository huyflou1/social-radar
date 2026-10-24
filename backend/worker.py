"""Durable single-host worker. Every tick claims at most one bounded API page."""
import json
import time
import uuid
from datetime import datetime, timezone
import httpx
from .config import Settings
from .db import Database, matches
from .connectors import CONNECTORS, SourceError

class Collector:
    def __init__(self, settings, db, client=None):
        self.settings, self.db = settings, db
        self.client = client

    def run(self, job_id, manual=False):
        now, token = time.time(), uuid.uuid4().hex
        with self.db.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return {'status': 'not_found'}
            job = dict(row)
            # next_run applies to manual requests too: no bypass of backoff/rate limits.
            cooldown = db.execute('SELECT until FROM cooldowns WHERE platform=?',
                                  (job['platform'],)).fetchone()
            if job['lease_until'] > now or job['next_run'] > now or (cooldown and cooldown[0] > now):
                return {'status': 'deferred'}
            if not manual and not job['enabled']:
                return {'status': 'disabled'}
            credential = {'x': self.settings.x_token, 'threads': self.settings.threads_token,
                          'facebook': self.settings.facebook_token}[job['platform']]
            if not credential:
                return {'status': 'missing_credentials'}
            if job['platform'] == 'facebook' and not self.settings.graph_version:
                return {'status': 'facebook_configuration_required'}
            if job['platform'] == 'x':
                day = datetime.now(timezone.utc).date().isoformat()
                db.execute('INSERT OR IGNORE INTO usage VALUES (?,?,0)', (day, 'x'))
                used = db.execute('SELECT reserved FROM usage WHERE day=? AND platform=?', (day, 'x')).fetchone()[0]
                if used + self.settings.page_size > self.settings.x_daily_read_cap:
                    return {'status': 'daily_read_cap'}
                # Reserve worst case before network I/O; failed attempts remain counted.
                db.execute('UPDATE usage SET reserved=reserved+? WHERE day=? AND platform=?',
                           (self.settings.page_size, day, 'x'))
            db.execute('UPDATE runs SET status=?,finished_at=? WHERE job_id=? AND status=?',
                       ('abandoned', now, job_id, 'running'))
            db.execute('UPDATE jobs SET lease_until=?,lease_token=? WHERE id=?', (now+120, token, job_id))
            run_id = db.execute('INSERT INTO runs(job_id,started_at,status) VALUES (?,?,?)',
                                (job_id, now, 'running')).lastrowid
        try:
            if self.client:
                page = CONNECTORS[job['platform']](self.settings, self.client).fetch(job)
            else:
                with httpx.Client(follow_redirects=False) as client:
                    page = CONNECTORS[job['platform']](self.settings, client).fetch(job)
            if len(page.posts) > self.settings.page_size:
                raise SourceError('invalid_source_page_size')
            inserted, linked = 0, 0
            with self.db.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                owner = db.execute('SELECT lease_token FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
                if owner != token:
                    return {'status': 'lease_lost'}
                for post in page.posts:
                    if not post['external_id'] or not isinstance(post['text'], str):
                        raise SourceError('invalid_source_payload')
                    exists = db.execute('SELECT id FROM posts WHERE platform=? AND external_id=?',
                                         (post['platform'], post['external_id'])).fetchone()
                    inserted += not bool(exists)
                    fields = ['platform','external_id','author','text','published_at','url',
                              'likes','comments','shares','language','content_type']
                    db.execute('''INSERT INTO posts ('''+','.join(fields)+''',collected_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,external_id)
                        DO UPDATE SET author=excluded.author,text=excluded.text,
                        published_at=excluded.published_at,url=excluded.url,likes=excluded.likes,
                        comments=excluded.comments,shares=excluded.shares,language=excluded.language,
                        content_type=excluded.content_type,collected_at=excluded.collected_at''',
                        [post.get(k) for k in fields]+[time.time()])
                    pid = db.execute('SELECT id FROM posts WHERE platform=? AND external_id=?',
                                     (post['platform'], post['external_id'])).fetchone()[0]
                    if job['platform'] != 'facebook' or matches(post['text'], job['query']):
                        db.execute('INSERT OR IGNORE INTO job_posts VALUES (?,?)', (job_id, pid))
                        linked += 1
                newest = job.get('newest_id')
                if page.newest_id:
                    newest = str(max(int(newest or '0'), int(page.newest_id)))
                since = job.get('since_id')
                if job['platform'] == 'x' and not page.cursor:
                    since, newest = newest or since, None
                db.execute('''UPDATE jobs SET cursor=?,since_id=?,newest_id=?,failures=0,last_error=NULL,
                   lease_until=0,lease_token=NULL,next_run=? WHERE id=?''',
                   (page.cursor, since, newest, time.time()+job['interval_seconds'], job_id))
                db.execute('''UPDATE runs SET finished_at=?,status=?,fetched=?,inserted=? WHERE id=?''',
                           (time.time(), 'success', len(page.posts), inserted, run_id))
            return {'status': 'success', 'fetched': len(page.posts), 'inserted': inserted,
                    'linked': linked, 'has_more': bool(page.cursor), 'run_id': run_id}
        except Exception as exc:
            error = exc if isinstance(exc, SourceError) else SourceError('invalid_source_payload')
            failures = job['failures']+1
            delay = max(error.retry_after, min(3600, 30*2**min(failures, 7)))
            terminal = error.code in ('permission_denied','credits_required','upstream_request_rejected',
                                     'upstream_payload_error','facebook_configuration_required') or failures >= 5
            with self.db.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                owner = db.execute('SELECT lease_token FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
                if owner != token:
                    return {'status': 'lease_lost'}
                db.execute('''UPDATE jobs SET failures=?,last_error=?,next_run=?,lease_until=0,
                    lease_token=NULL,enabled=CASE WHEN ? THEN 0 ELSE enabled END WHERE id=?''',
                    (failures, error.code, time.time()+delay, terminal, job_id))
                if error.code == 'rate_limited':
                    db.execute('''INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(platform)
                      DO UPDATE SET until=max(until,excluded.until)''', (job['platform'], time.time()+delay))
                db.execute('UPDATE runs SET status=?,finished_at=?,error_code=? WHERE id=?',
                           ('error', time.time(), error.code, run_id))
            return {'status': 'error', 'error_code': error.code, 'retry_after': delay,
                    'paused': terminal, 'run_id': run_id}

    def tick(self):
        now = time.time()
        for job in self.db.jobs():
            if job['enabled'] and job['next_run'] <= now and job['lease_until'] <= now:
                return self.run(job['id'])
        return {'status': 'idle'}

def main():
    settings = Settings.from_env()
    if len(settings.app_token) < 24:
        raise SystemExit('Set RADAR_APP_TOKEN with at least 24 characters.')
    collector = Collector(settings, Database(settings.db_path))
    while True:
        try:
            result = collector.tick()
            if result['status'] != 'idle':
                print(json.dumps(result), flush=True)
        except Exception:
            print('{"status":"worker_error"}', flush=True)
        time.sleep(5)

if __name__ == '__main__':
    main()
