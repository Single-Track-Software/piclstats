"""First-party usage log: what pages get used, by how many people, how fast.

Recorded from middleware after each response, off the request thread. The
visitor id is sha256(daily salt + ip + user agent)[:16]; the salt derives
from the session secret and the UTC date, so the same person is one
visitor for a day and nothing identifying is stored. Query strings keep
only allow-listed parameters (season, view, event id, category, sort).
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import text

logger = logging.getLogger(__name__)

_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage")

KEEP_DAYS = 180
KEEP_PARAMS = {
    "season",
    "view",
    "event_id",
    "category",
    "division",
    "gender",
    "metric",
    "dir",
    "tab",
    "course_id",
    "check",
    "q",
}
_BOT = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|wget|python-requests|httpx", re.I)

# (regex over the path, route name, group index of the entity or None)
_ROUTES: list[tuple[re.Pattern[str], str, int | None]] = [
    (re.compile(r"^/$"), "home", None),
    (re.compile(r"^/rider/(\d+)/forecast$"), "forecast", 1),
    (re.compile(r"^/rider/(\d+)$"), "rider", 1),
    (re.compile(r"^/riders$"), "riders", None),
    (re.compile(r"^/team/(.+)$"), "team", 1),
    (re.compile(r"^/teams$"), "teams", None),
    (re.compile(r"^/leaderboard$"), "leaderboard", None),
    (re.compile(r"^/states$"), "states", None),
    (re.compile(r"^/results$"), "results", None),
    (re.compile(r"^/racechart$"), "results", None),
    (re.compile(r"^/course/(\d+)$"), "course", 1),
    (re.compile(r"^/courses$"), "courses", None),
    (re.compile(r"^/local(/.*)?$"), "local", None),
    (re.compile(r"^/staging(/sheet)?(\.csv)?$"), "staging", None),
    (re.compile(r"^/login$"), "login", None),
    (re.compile(r"^/(forgot|reset|invite)(/.*)?$"), "auth", None),
    (re.compile(r"^/admin(/.*)?$"), "admin", None),
]


def classify(path: str) -> tuple[str, str | None]:
    """(route name, entity) for a request path; ('other', None) if unknown."""
    for pattern, route, group in _ROUTES:
        m = pattern.match(path)
        if m:
            entity = m.group(group) if group else None
            return route, (entity[:120] if entity else None)
    return "other", None


# Paths whose segments are access tokens: never written to the log, since a
# dump of page_views must not yield a working link.
_TOKEN_PATHS = ("/timing/", "/invite/", "/reset/")


def should_log(method: str, path: str, status: int) -> bool:
    return (
        method == "GET"
        and not path.startswith("/static")
        and not path.startswith(_TOKEN_PATHS)
        # JSON for scripts and the page's own lookups, not page views
        and not path.startswith("/api/")
        and status < 500
        and path not in ("/favicon.ico", "/healthz")
    )


def visitor_id(
    ip: str | None, user_agent: str | None, secret: str, today: date | None = None
) -> str:
    day = (today or date.today()).isoformat()
    salt = hashlib.sha256(f"{secret}|{day}".encode()).hexdigest()
    return hashlib.sha256(f"{salt}|{ip or ''}|{user_agent or ''}".encode()).hexdigest()[:16]


def kept_query(query: str) -> str | None:
    pairs = [(k, v[:80]) for k, v in parse_qsl(query, keep_blank_values=False) if k in KEEP_PARAMS]
    return urlencode(pairs) or None


def referrer_host(referrer: str | None, own_host: str | None) -> str | None:
    if not referrer:
        return None
    host = (urlsplit(referrer).hostname or "").lower()
    if not host or (own_host and host == own_host.split(":")[0].lower()):
        return None
    return host[:120]


def is_bot(user_agent: str | None) -> bool:
    return not user_agent or bool(_BOT.search(user_agent))


def record(row: dict) -> None:
    """Queue one page view for the writer thread; never blocks the request."""
    row.setdefault("view_id", None)
    _writer.submit(_write, row)


# ── Browser confirmation ────────────────────────────────────────────────
# Each HTML page view gets a random id, embedded in the page (base.html). A
# small script posts it back to /v once a real, visible, non-automated
# browser shows the page and someone interacts with it or keeps it on screen
# for a few seconds. Link previewers, prefetches and most bots never do.

CONFIRM_WITHIN_MINUTES = 30
CONFIRM_VIA = frozenset({"interaction", "dwell"})
_VIEW_ID = re.compile(r"^[A-Za-z0-9_-]{20,40}$")


def new_view_id() -> str:
    return secrets.token_urlsafe(16)


def valid_view_id(value: object) -> bool:
    return isinstance(value, str) and bool(_VIEW_ID.match(value))


def confirm(view_id: str, visitors: list[str], via: str) -> None:
    """Queue a confirmation behind any pending page-view insert.

    The writer is a single thread, so a confirmation always runs after the
    insert of the view it confirms, even when the beacon arrives at once.
    """
    _writer.submit(_confirm, view_id, visitors, via)


def _confirm(view_id: str, visitors: list[str], via: str) -> None:
    from piclstats.db.engine import get_session

    try:
        with get_session() as s:
            s.execute(
                text("""
                UPDATE page_views
                SET confirmed_at = now(), confirmed_via = :via
                WHERE view_id = :view_id
                  AND confirmed_at IS NULL
                  AND visitor = ANY(:visitors)
                  AND ts >= now() - make_interval(mins => :mins)
                """),
                {
                    "view_id": view_id,
                    "visitors": visitors,
                    "via": via,
                    "mins": CONFIRM_WITHIN_MINUTES,
                },
            )
            s.commit()
    except Exception:
        logger.debug("page view not confirmed", exc_info=True)


def _write(row: dict) -> None:
    from piclstats.db.engine import get_session

    try:
        with get_session() as s:
            s.execute(
                text("""
                INSERT INTO page_views
                    (route, path, query, entity, status, duration_ms, visitor, user_id, referrer,
                     is_bot, view_id)
                VALUES (:route, :path, :query, :entity, :status, :duration_ms, :visitor, :user_id,
                        :referrer, :is_bot, :view_id)
                """),
                row,
            )
            if random.random() < 0.001:  # occasional retention sweep, no scheduler needed
                s.execute(
                    text("DELETE FROM page_views WHERE ts < now() - make_interval(days => :d)"),
                    {"d": KEEP_DAYS},
                )
            s.commit()
    except Exception:  # logging must never take a page down
        logger.debug("page view not recorded", exc_info=True)
