import csv
import io
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from .config import Settings
from .db import Database
from .worker import Collector

class JobInput(BaseModel):
    platform: Literal['x', 'threads', 'facebook']
    query: str = Field(min_length=1, max_length=512)
    page_id: str = Field(default='', max_length=64)
    interval_seconds: int = Field(default=900, ge=60, le=604800)

    @model_validator(mode='after')
    def valid_scope(self):
        self.query = self.query.strip()
        if not self.query:
            raise ValueError('Query cannot be blank')
        if self.platform == 'facebook' and not self.page_id.isdecimal():
            raise ValueError('Facebook requires a numeric Page ID')
        return self

class ScheduleInput(BaseModel):
    enabled: bool

class BookmarkInput(BaseModel):
    saved: bool

def search_filters(q: str = Query('', max_length=512), exclude: str = Query('', max_length=512),
                   platform: str = Query('', pattern='^(|x|facebook|threads)$'),
                   min_engagement: int = Query(0, ge=0), days: int = Query(30, ge=1, le=3650),
                   language: str = Query('', max_length=12),
                   content_type: str = Query('', pattern='^(|text|media|unknown)$'),
                   saved: bool = False, job_id: int | None = Query(None, ge=1)):
    return dict(query=q, exclude=exclude, platform=platform, min_engagement=min_engagement,
                days=days, language=language, content_type=content_type, saved=saved, job_id=job_id)

def create_app(settings=None, client=None):
    settings = settings or Settings.from_env()
    @asynccontextmanager
    async def lifespan(app):
        if len(settings.app_token) < 24:
            raise RuntimeError('Set RADAR_APP_TOKEN with at least 24 characters.')
        yield
    app = FastAPI(title='Social Radar API', version='0.2.0', lifespan=lifespan)
    db = Database(settings.db_path)
    collector = Collector(settings, db, client)
    app.state.db, app.state.collector = db, collector

    def auth(authorization: str = Header(default='')):
        if len(settings.app_token) < 24:
            raise HTTPException(503, 'Server token is not configured')
        if not secrets.compare_digest(authorization, 'Bearer '+settings.app_token):
            raise HTTPException(401, 'Authentication required')

    def require_job(job_id):
        with db.connection() as conn:
            if not conn.execute('SELECT id FROM jobs WHERE id=?', (job_id,)).fetchone():
                raise HTTPException(404, 'Job not found')

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': 'live', 'version': '0.2.0'}

    @app.get('/api/sources', dependencies=[Depends(auth)])
    def sources():
        return {'sources': [
          {'platform': p, 'credential_configured': bool(token),
           'access': 'unverified', 'scope': scope}
          for p, token, scope in [
            ('x', settings.x_token, 'Recent Search: last 7 days'),
            ('threads', settings.threads_token, 'Keyword Search: permission-dependent; unverified adapter'),
            ('facebook', settings.facebook_token, 'Configured Page posts only; unverified adapter')]],
          'x_daily_read_cap': settings.x_daily_read_cap, 'page_size': settings.page_size,
          'note': 'Configured credentials do not establish permissions. Inspect successful runs and provider console.'}

    @app.get('/api/posts', dependencies=[Depends(auth)])
    def posts(filters=Depends(search_filters), limit: int = Query(100, ge=1, le=1000),
              offset: int = Query(0, ge=0)):
        return db.search(**filters, limit=limit, offset=offset)

    @app.get('/api/reports.csv', dependencies=[Depends(auth)])
    def report(filters=Depends(search_filters), limit: int = Query(10000, ge=1, le=10000)):
        result = db.search(**filters, limit=limit)
        out = io.StringIO()
        writer = csv.writer(out)
        fields = ['platform','external_id','author','text','url','published_at',
                  'collected_at','likes','comments','shares','language','content_type']
        writer.writerow(fields)
        def safe(value):
            text = '' if value is None else str(value)
            return "'"+text if text.lstrip().startswith(('=', '+', '-', '@')) else text
        for row in result['items']:
            writer.writerow([safe(row[field]) for field in fields])
        return Response('\ufeff'+out.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="social-radar-live.csv"',
                                 'X-Total-Count': str(result['total']),
                                 'X-Report-Truncated': str(result['total']>limit).lower()})

    @app.post('/api/jobs', dependencies=[Depends(auth)], status_code=201)
    def create_job(payload: JobInput):
        return {'id': db.create_job(payload.model_dump()), 'enabled': False}

    @app.get('/api/jobs', dependencies=[Depends(auth)])
    def jobs():
        # Do not disclose upstream cursors or lease tokens to browsers.
        hidden = {'cursor','since_id','newest_id','lease_token'}
        return {'items': [{k:v for k,v in j.items() if k not in hidden} for j in db.jobs()]}

    @app.patch('/api/jobs/{job_id}', dependencies=[Depends(auth)])
    def schedule(job_id: int, payload: ScheduleInput):
        require_job(job_id)
        with db.connection() as conn:
            conn.execute('UPDATE jobs SET enabled=? WHERE id=?', (payload.enabled, job_id))
        return {'id': job_id, 'enabled': payload.enabled}

    @app.post('/api/jobs/{job_id}/run', dependencies=[Depends(auth)])
    def run(job_id: int):
        require_job(job_id)
        return collector.run(job_id, manual=True)

    @app.get('/api/runs', dependencies=[Depends(auth)])
    def runs(limit: int = Query(50, ge=1, le=500)):
        with db.connection() as conn:
            return {'items': [dict(r) for r in conn.execute('SELECT * FROM runs ORDER BY id DESC LIMIT ?', (limit,))]}

    @app.get('/api/usage', dependencies=[Depends(auth)])
    def usage():
        with db.connection() as conn:
            return {'items': [dict(r) for r in conn.execute('SELECT * FROM usage ORDER BY day DESC LIMIT 31')],
                    'note': 'Conservative local reservations, not actual provider billing. UTC days.'}

    @app.put('/api/posts/{post_id}/bookmark', dependencies=[Depends(auth)])
    def bookmark(post_id: int, payload: BookmarkInput):
        with db.connection() as conn:
            if not conn.execute('SELECT id FROM posts WHERE id=?', (post_id,)).fetchone():
                raise HTTPException(404, 'Post not found')
            if payload.saved:
                conn.execute('INSERT OR IGNORE INTO bookmarks VALUES (?)', (post_id,))
            else:
                conn.execute('DELETE FROM bookmarks WHERE post_id=?', (post_id,))
        return {'saved': payload.saved}

    static = Path(__file__).resolve().parent.parent/'dist'
    app.mount('/', StaticFiles(directory=static, html=True), name='ui')
    return app

app = create_app()
