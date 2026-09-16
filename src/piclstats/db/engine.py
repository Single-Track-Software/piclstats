"""SQLAlchemy engine and session factory."""

from functools import lru_cache
from typing import Any, cast

from sqlalchemy import create_engine, CursorResult, Engine
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session, sessionmaker

from piclstats.config import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # pool_pre_ping guards against stale connections after the app machine
    # auto-stops (min_machines_running=0) and the Fly Postgres link over
    # .flycast drops idle conns; pool_recycle caps connection age.
    connect_args: dict[str, str] = {}
    if settings.statement_timeout_ms > 0:
        # psycopg passes `options` straight to the server as startup params.
        connect_args["options"] = f"-c statement_timeout={int(settings.statement_timeout_ms)}"
    return create_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=300,
        # uvicorn runs sync endpoints on a 40-thread pool; with the default
        # 5+10 connections and a 30 s wait, one slow page turned into 30 s
        # stalls for everyone else. Fail fast instead.
        pool_size=10,
        max_overflow=10,
        pool_timeout=5,
        connect_args=connect_args,
    )


def get_session() -> Session:
    return sessionmaker(bind=get_engine())()


def rowcount(result: Result[Any]) -> int:
    """Rows affected by a DML statement.

    Session.execute() is typed as returning Result, but every INSERT/UPDATE/
    DELETE actually returns a CursorResult, which is where rowcount lives.
    """
    return cast("CursorResult[Any]", result).rowcount
