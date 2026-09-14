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
    return create_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def get_session() -> Session:
    return sessionmaker(bind=get_engine())()


def rowcount(result: Result[Any]) -> int:
    """Rows affected by a DML statement.

    Session.execute() is typed as returning Result, but every INSERT/UPDATE/
    DELETE actually returns a CursorResult, which is where rowcount lives.
    """
    return cast("CursorResult[Any]", result).rowcount
