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
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from piclstats.db import api_keys_store
from piclstats.db.engine import get_session
from piclstats.quality.keys import team_key

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


def _team_in_scope(key: dict, team: str) -> str:
    """The key's own spelling of `team`, or 403 if the key doesn't cover it."""
    wanted = team_key(team)
    for name in key["team_names"]:
        if team_key(name) == wanted:
            return name
    raise HTTPException(status_code=403, detail="This key doesn't cover that team")


@router.get("/teams")
def teams(key: dict = Depends(require_api_key)) -> dict:
    """The teams this key can read, with their digest URLs."""
    return {
        "teams": [
            {"name": t, "digest_url": f"/api/v1/teams/{t.replace(' ', '%20')}/digest"}
            for t in key["team_names"]
        ]
    }


@router.get("/teams/{team}/digest")
def team_digest(
    team: str,
    since: date | None = Query(
        None, description="Races on or after this date (YYYY-MM-DD); default: the last 8 days"
    ),
    key: dict = Depends(require_api_key),
) -> dict[str, Any]:
    """A team's races since a date: each rider's result, change since their
    previous race, current conference standing, named highlights, team
    totals, and a Markdown summary built from those facts (web/digest.py)."""
    from piclstats.web import digest, queries
    from piclstats.web.standings import rank_by_total, standing_in

    name = _team_in_scope(key, team)
    since = since or (date.today() - timedelta(days=8))
    with get_session() as s:
        season = queries.current_season(s)
        rows = queries.team_race_rows(s, name, season) if season else []
        cache: dict[tuple[str, str, str], dict[int, dict[str, Any]]] = {}

        def standings(conference: str, division: str, gender: str) -> dict[int, dict[str, Any]]:
            k = (conference, division, gender)
            if k not in cache:
                ranked = rank_by_total(
                    queries.leaderboard(
                        s,
                        season,
                        division,
                        gender,
                        "total_points",
                        limit=None,
                        conferences=[conference],
                    )
                )
                cache[k] = {r["rider_id"]: standing_in(ranked, r["rider_id"]) or {} for r in ranked}
            return cache[k]

        return digest.build(name, rows, since, standings)
