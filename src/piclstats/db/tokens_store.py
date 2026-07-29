"""One-time invite and password-reset tokens.

Mirrors users_store.py: thin functions over get_session() returning plain dicts.

Two rules the rest of the app depends on:

1. Only the SHA-256 *hash* of a token is stored. The raw token exists once, in
   the link handed to the user. A dump of this table yields no working links.
2. Consuming a token is one-shot — `consume()` stamps used_at, and every lookup
   filters on used_at IS NULL, so a forwarded or re-opened link fails closed.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from piclstats.db.engine import get_session
from piclstats.db.tables import auth_tokens

INVITE = "invite"
RESET = "reset"

# Invites are handed out deliberately and may sit in an inbox over a weekend.
# Resets answer a "locked out right now" request, so they expire fast.
INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=1)

_COLS = (
    auth_tokens.c.id,
    auth_tokens.c.purpose,
    auth_tokens.c.email,
    auth_tokens.c.role,
    auth_tokens.c.user_id,
    auth_tokens.c.created_by,
    auth_tokens.c.expires_at,
    auth_tokens.c.used_at,
    auth_tokens.c.created_at,
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _norm(email: str) -> str:
    return email.strip().lower()


def create(
    *,
    purpose: str,
    email: str,
    role: str | None = None,
    user_id: int | None = None,
    created_by: int | None = None,
    ttl: timedelta | None = None,
) -> str:
    """Mint a token, store only its hash, and return the raw token to the caller.

    The raw value is never recoverable afterwards — if the link is lost, issue
    a new one.
    """
    token = secrets.token_urlsafe(32)
    if ttl is None:
        ttl = INVITE_TTL if purpose == INVITE else RESET_TTL
    with get_session() as s:
        s.execute(
            auth_tokens.insert().values(
                token_hash=hash_token(token),
                purpose=purpose,
                email=_norm(email),
                role=role,
                user_id=user_id,
                created_by=created_by,
                expires_at=datetime.now(timezone.utc) + ttl,
            )
        )
        s.commit()
    return token


def get_valid(token: str, purpose: str) -> dict | None:
    """Resolve a raw token to its unused, unexpired row of the right purpose."""
    with get_session() as s:
        row = (
            s.execute(
                select(*_COLS).where(
                    auth_tokens.c.token_hash == hash_token(token),
                    auth_tokens.c.purpose == purpose,
                    auth_tokens.c.used_at.is_(None),
                    auth_tokens.c.expires_at > datetime.now(timezone.utc),
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def consume(token_id: int) -> None:
    """Mark a token spent. Call inside the same request that acts on it."""
    with get_session() as s:
        s.execute(
            update(auth_tokens)
            .where(auth_tokens.c.id == token_id, auth_tokens.c.used_at.is_(None))
            .values(used_at=datetime.now(timezone.utc))
        )
        s.commit()


def invalidate_outstanding(email: str, purpose: str) -> None:
    """Spend any live tokens for an address.

    Called when a new one is issued, so a coach who requests two resets can't
    leave an older link working, and a re-sent invite retires the previous one.
    """
    with get_session() as s:
        s.execute(
            update(auth_tokens)
            .where(
                auth_tokens.c.email == _norm(email),
                auth_tokens.c.purpose == purpose,
                auth_tokens.c.used_at.is_(None),
            )
            .values(used_at=datetime.now(timezone.utc))
        )
        s.commit()


def list_pending_invites() -> list[dict]:
    """Live invites, for the admin users page."""
    with get_session() as s:
        rows = (
            s.execute(
                select(*_COLS)
                .where(
                    auth_tokens.c.purpose == INVITE,
                    auth_tokens.c.used_at.is_(None),
                    auth_tokens.c.expires_at > datetime.now(timezone.utc),
                )
                .order_by(auth_tokens.c.created_at.desc())
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def revoke(token_id: int) -> None:
    """Admin cancelling an invite that hasn't been accepted."""
    consume(token_id)
