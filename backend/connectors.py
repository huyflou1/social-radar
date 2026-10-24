"""Read-only connectors. One bounded page per call; no scraped sessions or cookies."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
import httpx

class SourceError(Exception):
    def __init__(self, code, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)

@dataclass
class Page:
    posts: list
    cursor: str | None = None
    newest_id: str | None = None

def timestamp(value):
    if not value:
        raise SourceError('invalid_source_timestamp')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        raise SourceError('invalid_source_timestamp') from None

def retry_delay(headers):
    now = time.time()
    values = [60.0]
    try:
        values.append(float(headers.get('retry-after', '0')))
    except ValueError:
        try:
            values.append(parsedate_to_datetime(headers['retry-after']).timestamp() - now)
        except (ValueError, KeyError, TypeError):
            pass
    try:
        values.append(float(headers.get('x-rate-limit-reset', '0')) - now)
    except ValueError:
        pass
    return min(86400, max(values))

class Connector:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client

    def request(self, url, token, params):
        if not token:
            raise SourceError('missing_credentials')
        try:
            response = self.client.get(url, params=params,
                                       headers={'Authorization': 'Bearer ' + token}, timeout=20)
        except httpx.RequestError:
            # Never persist exception text: it may contain a signed URL or token.
            raise SourceError('network_error', 60) from None
        if response.status_code == 429:
            raise SourceError('rate_limited', retry_delay(response.headers))
        if response.status_code in (401, 403):
            raise SourceError('permission_denied')
        if response.status_code == 402:
            raise SourceError('credits_required')
        if response.status_code >= 500:
            raise SourceError('upstream_unavailable', 60)
        if not 200 <= response.status_code < 300:
            raise SourceError('upstream_request_rejected')
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get('error') or data.get('errors'):
                raise SourceError('upstream_payload_error')
            if not isinstance(data.get('data', []), list):
                raise SourceError('invalid_source_payload')
            return data
        except ValueError:
            raise SourceError('invalid_source_payload') from None

class XConnector(Connector):
    def fetch(self, job):
        params = {'query': job['query'], 'max_results': self.settings.page_size,
                  'tweet.fields': 'created_at,author_id,lang,public_metrics,attachments',
                  'sort_order': 'recency'}
        if job.get('cursor'):
            params['next_token'] = job['cursor']
        if job.get('since_id'):
            params['since_id'] = job['since_id']
        data = self.request('https://api.x.com/2/tweets/search/recent', self.settings.x_token, params)
        posts = []
        for item in data.get('data', []):
            m = item.get('public_metrics', {})
            posts.append({'platform': 'x', 'external_id': str(item['id']),
                          'author': item.get('author_id', ''), 'text': item['text'],
                          'published_at': timestamp(item.get('created_at')),
                          'url': 'https://x.com/i/web/status/' + str(item['id']),
                          'likes': m.get('like_count'), 'comments': m.get('reply_count'),
                          'shares': m.get('retweet_count', m.get('repost_count')),
                          'language': item.get('lang'),
                          'content_type': 'media' if item.get('attachments') else 'text'})
        meta = data.get('meta', {})
        return Page(posts, meta.get('next_token'), meta.get('newest_id'))

class ThreadsConnector(Connector):
    def fetch(self, job):
        params = {'q': job['query'], 'search_type': 'RECENT', 'limit': self.settings.page_size,
                  'fields': 'id,text,timestamp,permalink,username,media_type'}
        if job.get('cursor'):
            params['after'] = job['cursor']
        data = self.request('https://graph.threads.net/v1.0/keyword_search',
                            self.settings.threads_token, params)
        posts = [{'platform': 'threads', 'external_id': str(p['id']),
                  'author': p.get('username', ''), 'text': p.get('text', ''),
                  'published_at': timestamp(p.get('timestamp')), 'url': p.get('permalink', ''),
                  'likes': None, 'comments': None, 'shares': None, 'language': None,
                  'content_type': 'text' if p.get('media_type') == 'TEXT_POST' else 'media'}
                 for p in data.get('data', [])]
        paging = data.get('paging', {})
        return Page(posts, paging.get('cursors', {}).get('after') if paging.get('next') else None)

class FacebookConnector(Connector):
    def fetch(self, job):
        version = self.settings.graph_version
        if not re.fullmatch(r'v\d+\.\d+', version) or not re.fullmatch(r'\d+', job.get('page_id', '')):
            raise SourceError('facebook_configuration_required')
        params = {'fields': 'id,message,created_time,permalink_url,from',
                  'limit': self.settings.page_size}
        if job.get('cursor'):
            params['after'] = job['cursor']
        # Scope is one explicitly configured Page, NOT global Facebook keyword search.
        data = self.request(f"https://graph.facebook.com/{version}/{job['page_id']}/posts",
                            self.settings.facebook_token, params)
        posts = [{'platform': 'facebook', 'external_id': str(p['id']),
                  'author': p.get('from', {}).get('name', job['page_id']),
                  'text': p.get('message', ''), 'published_at': timestamp(p.get('created_time')),
                  'url': p.get('permalink_url', ''), 'likes': None, 'comments': None,
                  'shares': None, 'language': None, 'content_type': 'unknown'}
                 for p in data.get('data', [])]
        paging = data.get('paging', {})
        return Page(posts, paging.get('cursors', {}).get('after') if paging.get('next') else None)

CONNECTORS = {'x': XConnector, 'threads': ThreadsConnector, 'facebook': FacebookConnector}
