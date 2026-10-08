"""API keys: create, look up, list, revoke.

Mirrors tokens_store.py: only the SHA-256 hash of a key is stored, and the
raw key exists once, on the admin page that created it. A dump of this table
yields no working keys.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from piclstats.db.engine import get_session
from piclstats.db.tables import api_keys
from piclstats.quality.keys import team_key

KEY_PREFIX = "pcls_"
# last_used_at is a hint for the admin page, not an audit log: write it at
# most this often per key so a busy client doesn't write on every request.
_TOUCH_EVERY = timedelta(minutes=5)

_COLS = (
    api_keys.c.id,
    api_keys.c.name,
    api_keys.c.prefix,
    api_keys.c.team_names,
    api_keys.c.team_keys,
    api_keys.c.created_by,
    api_keys.c.created_at,
    api_keys.c.last_used_at,
    api_keys.c.revoked_at,
)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create(name: str, team_names: list[str], created_by: int | None = None) -> str:
    """Store a new key for these teams and return the raw key (shown once)."""
    names = sorted({t.strip() for t in team_names if t and t.strip()})
    if not name.strip() or not names:
        raise ValueError("A key needs a name and at least one team")
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    keys = sorted({k for k in (team_key(t) for t in names) if k})
    with get_session() as s:
        s.execute(
            api_keys.insert().values(
                name=name.strip(),
                prefix=raw[: len(KEY_PREFIX) + 6],
                key_hash=hash_key(raw),
                team_names=names,
                team_keys=keys,
                created_by=created_by,
            )
        )
        s.commit()
    return raw


def lookup(raw: str) -> dict | None:
    """The active key matching this raw value, or None (unknown or revoked)."""
    if not raw or not raw.startswith(KEY_PREFIX):
        return None
    with get_session() as s:
        row = (
            s.execute(
                select(*_COLS).where(
                    api_keys.c.key_hash == hash_key(raw), api_keys.c.revoked_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        last = row["last_used_at"]
        if last is None or now - last > _TOUCH_EVERY:
            s.execute(update(api_keys).where(api_keys.c.id == row["id"]).values(last_used_at=now))
            s.commit()
        return dict(row)


def list_all() -> list[dict]:
    with get_session() as s:
        rows = s.execute(select(*_COLS).order_by(api_keys.c.created_at.desc())).mappings().all()
    return [dict(r) for r in rows]


def revoke(key_id: int) -> None:
    with get_session() as s:
        s.execute(
            update(api_keys)
            .where(api_keys.c.id == key_id, api_keys.c.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        s.commit()
