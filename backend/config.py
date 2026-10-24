from dataclasses import dataclass
import os

@dataclass
class Settings:
    db_path: str = 'data/radar.sqlite3'
    app_token: str = ''
    x_token: str = ''
    threads_token: str = ''
    facebook_token: str = ''
    graph_version: str = ''
    x_daily_read_cap: int = 100
    page_size: int = 10

    @classmethod
    def from_env(cls):
        return cls(db_path=os.getenv('RADAR_DB', 'data/radar.sqlite3'),
                   app_token=os.getenv('RADAR_APP_TOKEN', ''),
                   x_token=os.getenv('X_BEARER_TOKEN', ''),
                   threads_token=os.getenv('THREADS_ACCESS_TOKEN', ''),
                   facebook_token=os.getenv('FACEBOOK_PAGE_TOKEN', ''),
                   graph_version=os.getenv('META_GRAPH_VERSION', ''),
                   x_daily_read_cap=max(0, int(os.getenv('X_DAILY_READ_CAP', '100'))),
                   page_size=max(10, min(100, int(os.getenv('COLLECT_PAGE_SIZE', '10')))))
