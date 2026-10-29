"""数据库层：engine / 会话工厂 / Base / 依赖注入 / SQLite 外键启用。

- 使用 SQLAlchemy 2.x 声明式基类 ``Base``；
- 通过 ``event.listens_for(Engine, "connect")`` 为每条 SQLite 连接执行
  ``PRAGMA foreign_keys=ON``（SQLite 默认不启用外键约束，必须显式开启）；
- engine 使用 ``check_same_thread=False`` 以配合 FastAPI 的线程模型。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """SQLAlchemy 2.x 声明式基类，所有 ORM 模型继承自此类。"""


# SQLite 需要关闭同线程检查，FastAPI 的依赖注入会在不同线程间复用连接。
_connect_args = {"check_same_thread": False} if settings.db_path.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """为每个 SQLite 连接启用外键约束。

    仅对 SQLite 连接生效；其他数据库方言直接跳过。
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：提供请求级数据库会话，请求结束后自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["Base", "engine", "SessionLocal", "get_db"]
