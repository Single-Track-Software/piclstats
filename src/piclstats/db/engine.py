"""SQLAlchemy engine and session factory."""

import logging
import time
from functools import lru_cache
from typing import Any, cast

import psycopg
from sqlalchemy import create_engine, event, CursorResult, Engine
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session, sessionmaker

from piclstats.config import settings

logger = logging.getLogger(__name__)

# New connections to Managed Postgres sometimes fail outright ("server closed
# the connection unexpectedly", 2026-10-02) while the server itself is fine.
# Retry a couple of times with a short pause rather than failing the page.
CONNECT_ATTEMPTS = 3
CONNECT_TIMEOUT_S = 5
_RETRY_PAUSE_S = 0.25


def _connect_with_retry(dialect: Any, conn_rec: Any, cargs: Any, cparams: Any) -> Any:
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return dialect.loaded_dbapi.connect(*cargs, **cparams)
        except psycopg.OperationalError as exc:
            if attempt == CONNECT_ATTEMPTS:
                raise
            logger.warning(
                "db connect failed (attempt %d/%d): %s",
                attempt,
                CONNECT_ATTEMPTS,
                str(exc).splitlines()[0],
            )
            time.sleep(_RETRY_PAUSE_S * attempt)
    raise AssertionError("unreachable")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # pool_pre_ping guards against stale connections after the app machine
    # auto-stops (min_machines_running=0) and the Fly Postgres link over
    # .flycast drops idle conns; pool_recycle caps connection age.
    # Without a connect timeout a connect that hangs (rather than failing)
    # holds a worker thread until the OS gives up, minutes later; enough of
    # those and every page hangs.
    connect_args: dict[str, Any] = {"connect_timeout": CONNECT_TIMEOUT_S}
    if settings.statement_timeout_ms > 0:
        # psycopg passes `options` straight to the server as startup params.
        connect_args["options"] = f"-c statement_timeout={int(settings.statement_timeout_ms)}"
    engine = create_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        # Reconnecting is the flaky part, so don't churn: pre-ping already
        # replaces a connection the server or network dropped.
        pool_recycle=1800,
        # uvicorn runs sync endpoints on a 40-thread pool; with the default
        # 5+10 connections and a 30 s wait, one slow page turned into 30 s
        # stalls for everyone else. Fail fast instead.
        pool_size=10,
        max_overflow=10,
        pool_timeout=5,
        connect_args=connect_args,
    )
    event.listen(engine, "do_connect", _connect_with_retry)
    return engine


def get_session() -> Session:
    return sessionmaker(bind=get_engine())()


def rowcount(result: Result[Any]) -> int:
    """Rows affected by a DML statement.

    Session.execute() is typed as returning Result, but every INSERT/UPDATE/
    DELETE actually returns a CursorResult, which is where rowcount lives.
    """
    return cast("CursorResult[Any]", result).rowcount
