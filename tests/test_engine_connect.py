"""Connect retry for the flaky Managed Postgres connect path (2026-10-02)."""

import psycopg
import pytest

from piclstats.db import engine as engine_mod


class _Dialect:
    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0
        self.loaded_dbapi = self

    def connect(self, *cargs, **cparams):
        self.calls += 1
        if self.calls <= self.failures:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return "conn"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(engine_mod.time, "sleep", lambda s: None)


def test_a_dropped_connect_is_retried():
    d = _Dialect(failures=2)
    assert engine_mod._connect_with_retry(d, None, (), {}) == "conn"
    assert d.calls == 3


def test_gives_up_after_the_last_attempt():
    d = _Dialect(failures=engine_mod.CONNECT_ATTEMPTS)
    with pytest.raises(psycopg.OperationalError):
        engine_mod._connect_with_retry(d, None, (), {})
    assert d.calls == engine_mod.CONNECT_ATTEMPTS


def test_engine_uses_the_retry_and_does_not_churn_connections():
    from sqlalchemy import event

    engine_mod.get_engine.cache_clear()
    try:
        eng = engine_mod.get_engine()
        assert event.contains(eng, "do_connect", engine_mod._connect_with_retry)
        assert eng.pool._recycle == 1800
    finally:
        engine_mod.get_engine.cache_clear()
