import json
import sqlite3
import time
import unicodedata
import re
from pathlib import Path
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY, platform TEXT NOT NULL, external_id TEXT NOT NULL,
 author TEXT NOT NULL, text TEXT NOT NULL, published_at TEXT NOT NULL,
 url TEXT NOT NULL, likes INTEGER, comments INTEGER, shares INTEGER,
 language TEXT, content_type TEXT NOT NULL, collected_at REAL NOT NULL,
 UNIQUE(platform, external_id));
CREATE INDEX IF NOT EXISTS posts_date ON posts(published_at);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, platform TEXT NOT NULL, query TEXT NOT NULL,
 page_id TEXT NOT NULL DEFAULT '', interval_seconds INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, next_run REAL NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
 cursor TEXT, since_id TEXT, newest_id TEXT, failures INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, started_at REAL NOT NULL,
 finished_at REAL, status TEXT NOT NULL, fetched INTEGER NOT NULL DEFAULT 0,
 inserted INTEGER NOT NULL DEFAULT 0, error_code TEXT);
CREATE TABLE IF NOT EXISTS job_posts (
 job_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
 PRIMARY KEY(job_id, post_id));
CREATE TABLE IF NOT EXISTS usage (
 day TEXT NOT NULL, platform TEXT NOT NULL, reserved INTEGER NOT NULL,
 PRIMARY KEY(day, platform));
CREATE TABLE IF NOT EXISTS cooldowns (platform TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bookmarks (post_id INTEGER PRIMARY KEY);
'''

def fold(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s.lower().replace('đ', 'd'))
                   if not unicodedata.combining(c))

def matches(text, query, exclude=''):
    text = fold(text)
    tokens = re.findall(r'"([^"]+)"|(\S+)', query)
    return all(fold(a or b) in text for a, b in tokens) and not any(
        fold(t.strip()) in text for t in exclude.split(',') if t.strip())

class Database:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        db.create_function('matches', 3, matches)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_job(self, values):
        with self.connection() as db:
            cur = db.execute('''INSERT INTO jobs
             (platform,query,page_id,interval_seconds,created_at) VALUES (?,?,?,?,?)''',
             (values['platform'], values['query'], values.get('page_id', ''),
              values['interval_seconds'], time.time()))
            return cur.lastrowid

    def jobs(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC')]

    def search(self, query='', exclude='', platform='', min_engagement=0, days=30,
               language='', content_type='', saved=False, job_id=None, limit=100, offset=0):
        clauses = ['matches(p.text,?,?)', 'p.published_at>=?']
        from datetime import datetime, timezone, timedelta
        params = [query, exclude, (datetime.now(timezone.utc)-timedelta(days=days)).isoformat()]
        if platform:
            clauses.append('p.platform=?'); params.append(platform)
        if min_engagement:
            clauses.append('(coalesce(p.likes,0)+coalesce(p.comments,0)+coalesce(p.shares,0))>=?')
            params.append(min_engagement)
        if language:
            clauses.append('p.language=?'); params.append(language)
        if content_type:
            clauses.append('p.content_type=?'); params.append(content_type)
        if saved:
            clauses.append('EXISTS(SELECT 1 FROM bookmarks b WHERE b.post_id=p.id)')
        if job_id is not None:
            clauses.append('EXISTS(SELECT 1 FROM job_posts j WHERE j.post_id=p.id AND j.job_id=?)')
            params.append(job_id)
        where = ' AND '.join(clauses)
        with self.connection() as db:
            total = db.execute('SELECT count(*) FROM posts p WHERE '+where, params).fetchone()[0]
            rows = db.execute('''SELECT p.*, EXISTS(SELECT 1 FROM bookmarks b WHERE b.post_id=p.id)
              AS saved FROM posts p WHERE '''+where+' ORDER BY p.published_at DESC,p.id DESC LIMIT ? OFFSET ?',
              params+[limit, offset]).fetchall()
        return {'total': total, 'items': [dict(r) for r in rows], 'limit': limit, 'offset': offset}
