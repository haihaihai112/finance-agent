"""MySQL／SQLite 使用同一仓储接口；本地 SQLite 便于零服务启动。"""

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def create_database_engine(database_url: str):
    options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            options["poolclass"] = StaticPool
    return create_engine(database_url, **options)
