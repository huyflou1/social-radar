"""Operator-run pilot. Dry-run by default; --execute authorizes one bounded API request."""
import argparse
import json
from datetime import datetime, timezone
from .config import Settings
from .db import Database
from .worker import Collector

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--query', required=True)
    parser.add_argument('--platform', choices=['x','threads','facebook'], default='x')
    parser.add_argument('--page-id', default='')
    parser.add_argument('--execute', action='store_true')
    args=parser.parse_args()
    settings=Settings.from_env()
    if len(settings.app_token)<24:
        raise SystemExit('Configure RADAR_APP_TOKEN (24+ characters).')
    credential={'x':settings.x_token,'threads':settings.threads_token,
                'facebook':settings.facebook_token}[args.platform]
    report={'checked_at':datetime.now(timezone.utc).isoformat(), 'platform':args.platform,
            'credential_configured':bool(credential), 'page_size':settings.page_size,
            'executed':False, 'permission_verified':False}
    if not args.execute:
        report['next']='Configure credentials, then use --execute for one potentially billable request.'
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if not credential:
        raise SystemExit('Missing platform credential. No request sent.')
    if not args.query.strip() or len(args.query)>512:
        raise SystemExit('Query must contain 1-512 characters.')
    if args.platform=='facebook' and not args.page_id.isdecimal():
        raise SystemExit('Numeric Facebook Page ID required.')
    db=Database(settings.db_path)
    job=db.create_job({'platform':args.platform,'query':args.query,'page_id':args.page_id,
                       'interval_seconds':900})
    result=Collector(settings,db).run(job,manual=True)
    report.update(job_id=job, result=result,
                  executed=result.get('status') not in ('missing_credentials','daily_read_cap',
                                                       'facebook_configuration_required','deferred'),
                  permission_verified=result.get('status')=='success')
    if result.get('status')=='success':
        with db.connection() as conn:
            posts=conn.execute('SELECT p.* FROM posts p JOIN job_posts j ON p.id=j.post_id WHERE j.job_id=?',
                               (job,)).fetchall()
        report['quality']={'linked_posts':len(posts),
                           'missing_url':sum(not p['url'] for p in posts),
                           'missing_author':sum(not p['author'] for p in posts),
                           'unknown_likes':sum(p['likes'] is None for p in posts),
                           'human_relevance_review':'pending'}
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
