"""Find new races on the league results page.

pamtb.org links every published race as ``https://my.raceresult.com/<id>/``
with the race name as the link text. Anything not in the code registry, not
already loaded, and not already recorded in ``discovered_events`` is new.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.scraper.registry import SEASONS

RESULTS_URL = "https://www.pamtb.org/results-standings"
_LINK = re.compile(r'href="https?://my\.raceresult\.com/(\d+)/?"[^>]*>\s*([^<]*?)\s*<', re.I)


@dataclass(frozen=True)
class Discovered:
    raceresult_id: int
    name: str


def parse_links(html: str) -> list[Discovered]:
    """Every distinct raceresult event linked on the page, in page order."""
    seen: set[int] = set()
    out: list[Discovered] = []
    for rid, name in _LINK.findall(html):
        eid = int(rid)
        if eid in seen:
            continue
        seen.add(eid)
        out.append(Discovered(eid, " ".join(name.split())))
    return out


def fetch_page(url: str = RESULTS_URL) -> str:
    resp = httpx.get(
        url,
        headers={"User-Agent": "piclstats/1.0 (+https://piclstats.com)"},
        follow_redirects=True,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def current_season(today: date | None = None) -> int:
    """PICL races September to November; the season is the calendar year."""
    return (today or date.today()).year


def find_new(session: Session, links: list[Discovered]) -> list[Discovered]:
    """Links that nothing knows about yet (registry, events, discovered_events)."""
    known: set[int] = {eid for ids in SEASONS.values() for eid in ids}
    known.update(r[0] for r in session.execute(text("SELECT raceresult_id FROM events")).all())
    known.update(
        r[0]
        for r in session.execute(
            text("SELECT raceresult_id FROM discovered_events WHERE status <> 'new'")
        ).all()
    )
    return [d for d in links if d.raceresult_id not in known]


def record(
    session: Session, d: Discovered, season: int, status: str, note: str | None = None
) -> None:
    session.execute(
        text("""
        INSERT INTO discovered_events (raceresult_id, season, name, source_url, status, note)
        VALUES (:id, :season, :name, :url, :status, :note)
        ON CONFLICT (raceresult_id) DO UPDATE
        SET status = EXCLUDED.status, note = EXCLUDED.note, updated_at = now()
        """),
        {
            "id": d.raceresult_id,
            "season": season,
            "name": d.name,
            "url": RESULTS_URL,
            "status": status,
            "note": note,
        },
    )


def next_event_order(session: Session, season: int) -> int:
    return session.execute(
        text("SELECT COALESCE(max(event_order), 0) + 1 FROM events WHERE season = :s"),
        {"s": season},
    ).scalar_one()
