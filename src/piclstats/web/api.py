"""Read-only JSON API for automated clients: /api/v1, team-scoped keys.

Callers send `Authorization: Bearer pcls_...`. A key only ever sees the
teams it was issued for (admin → API keys), and each key is rate limited.
There are deliberately no league-wide bulk endpoints, so a leaked key can't
be used to harvest the site.

The API is reached at piclstats.fly.dev/api/v1/... : Cloudflare's Bot Fight
Mode challenges every automated client on piclstats.com, and the origin lock
(web/edge.py) lets /api/v1 through so the key check here decides.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request

from piclstats.db import api_keys_store

router = APIRouter(prefix="/api/v1", tags=["api"])

# Per-key request budget: generous for a weekly digest job, tight enough that
# a leaked key can't be used to page through the site quickly.
RATE_LIMIT = 60
RATE_WINDOW_SECONDS = 60.0
_recent: dict[int, deque[float]] = defaultdict(deque)


def _rate_ok(key_id: int, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    hits = _recent[key_id]
    while hits and now - hits[0] > RATE_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        return False
    hits.append(now)
    return True


def require_api_key(request: Request) -> dict:
    """The calling key (with its team scope), or 401 / 429."""
    auth = request.headers.get("authorization", "")
    raw = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    key = api_keys_store.lookup(raw)
    if key is None:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _rate_ok(key["id"]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded; slow down")
    return key


@router.get("/me")
def me(key: dict = Depends(require_api_key)) -> dict:
    """Which key this is and the teams it can read: a connection check."""
    return {"key": key["name"], "teams": key["team_names"]}
