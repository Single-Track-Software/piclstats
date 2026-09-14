"""Key/value settings persisted in the settings table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from piclstats.db.engine import get_session
from piclstats.db.tables import settings as settings_table


def get_value(key: str, default=None):
    with get_session() as s:
        row = s.execute(select(settings_table.c.value).where(settings_table.c.key == key)).first()
        return row[0] if row else default


def set_value(key: str, value) -> None:
    stmt = insert(settings_table).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    with get_session() as s:
        s.execute(stmt)
        s.commit()


def get_forecast_config() -> dict:
    from piclstats.web.forecast import DEFAULT_CONFIG

    override = get_value("forecast_config", {}) or {}
    return {**DEFAULT_CONFIG, **override}
