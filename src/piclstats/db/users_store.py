"""CRUD helpers for login accounts in the users table.

Mirrors settings_store.py: thin functions over get_session() that return plain
dicts (never ORM rows), so callers don't hold a live session. Emails are stored
and looked up lower-cased so logins are case-insensitive.
"""

from __future__ import annotations

from sqlalchemy import select, update

from piclstats.db.engine import get_session
from piclstats.db.tables import users

_COLS = (
    users.c.id,
    users.c.email,
    users.c.name,
    users.c.password_hash,
    users.c.role,
    users.c.is_active,
    users.c.created_at,
    users.c.last_login_at,
)


def _norm(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(email: str) -> dict | None:
    with get_session() as s:
        row = s.execute(
            select(*_COLS).where(users.c.email == _norm(email))
        ).mappings().first()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_session() as s:
        row = s.execute(
            select(*_COLS).where(users.c.id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with get_session() as s:
        rows = s.execute(
            select(*_COLS).order_by(users.c.email)
        ).mappings().all()
    return [dict(r) for r in rows]


def create_user(email: str, name: str | None, password_hash: str, role: str) -> int:
    with get_session() as s:
        result = s.execute(
            users.insert().values(
                email=_norm(email), name=name or None,
                password_hash=password_hash, role=role,
            ).returning(users.c.id)
        )
        user_id = result.scalar_one()
        s.commit()
    return user_id


def set_password(user_id: int, password_hash: str) -> None:
    with get_session() as s:
        s.execute(
            update(users).where(users.c.id == user_id).values(password_hash=password_hash)
        )
        s.commit()


def set_role(user_id: int, role: str) -> None:
    with get_session() as s:
        s.execute(update(users).where(users.c.id == user_id).values(role=role))
        s.commit()


def set_active(user_id: int, is_active: bool) -> None:
    with get_session() as s:
        s.execute(update(users).where(users.c.id == user_id).values(is_active=is_active))
        s.commit()


def touch_last_login(user_id: int) -> None:
    from sqlalchemy import func

    with get_session() as s:
        s.execute(
            update(users).where(users.c.id == user_id).values(last_login_at=func.now())
        )
        s.commit()
