"""Teen Trail Corps bonus points, read from raceresult's season-overall lists.

The league keeps TTC hours in a spreadsheet fed from PitZone and loads the
bonus into its raceresult events; each conference's events carry a season
list ("03 - Season Overall Results|...") with a `TTCBonusPoints` column next
to the series score. The list on a conference's newest race is the current
snapshot, so `sync_season` walks a season's races newest first and keeps
the first bonus it sees for each rider and category.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from piclstats.db.tables import ttc_bonus
from piclstats.scraper import client

logger = logging.getLogger(__name__)

BONUS_FIELD = "TTCBonusPoints"
HOURS_FIELD = "TTC_Hours"


@dataclass(frozen=True)
class TTCRow:
    category: str  # e.g. "8th Grade - Male", as the results carry it
    bib: int
    name: str
    bonus: int
    hours: Decimal | None


def season_list_names(config: dict) -> list[str]:
    """Season-overall individual lists in an event config, TTC variants first."""
    names: list[str] = [i["Name"] for i in config.get("Lists", []) if "Name" in i]
    names += [i["Name"] for i in config.get("TabConfig", {}).get("Lists", []) if "Name" in i]
    found = [
        n for n in names if "season overall" in n.lower() and "indiv" in n.lower() and "|" in n
    ]
    return sorted(dict.fromkeys(found), key=lambda n: "ttc" not in n.lower())


def _groups(data: Any, label: str = ""):
    """(group label, rows) for every row list in raceresult's nested data."""
    if isinstance(data, dict):
        for key, value in data.items():
            yield from _groups(value, key)
    elif isinstance(data, list) and data and isinstance(data[0], list):
        yield label, data


def _category(label: str) -> str:
    """'#12_8th Grade - Female' -> '8th Grade - Female'."""
    return label.split("_", 1)[1] if label.startswith("#") and "_" in label else label


def _norm(name: str | None) -> str:
    return " ".join((name or "").upper().split())


def parse_list(payload: dict) -> list[TTCRow]:
    """Rows of one season list, or [] when it has no TTC bonus column."""
    fields: list[str] = payload.get("DataFields") or []
    if BONUS_FIELD not in fields or "BIB" not in fields:
        return []
    i_bib, i_bonus = fields.index("BIB"), fields.index(BONUS_FIELD)
    i_hours = fields.index(HOURS_FIELD) if HOURS_FIELD in fields else None
    i_name = next((i for i, f in enumerate(fields) if "DisplayName" in f), None)
    out: list[TTCRow] = []
    for label, rows in _groups(payload.get("data")):
        for row in rows:
            try:
                bib = int(row[i_bib])
                bonus = int(row[i_bonus] or 0)
            except (ValueError, TypeError, IndexError):
                continue
            hours = None
            if i_hours is not None:
                try:
                    hours = Decimal(str(row[i_hours]))
                except (InvalidOperation, IndexError):
                    hours = None
            name = str(row[i_name]) if i_name is not None else ""
            out.append(TTCRow(_category(label), bib, name, bonus, hours))
    return out


def sync_season(session: Session, season: int) -> dict[str, int]:
    """Replace the season's TTC bonus rows from raceresult; returns counts."""
    events = session.execute(
        text("""
        SELECT raceresult_id FROM events
        WHERE season = :season AND is_published AND event_type = 'points'
        ORDER BY event_order DESC
    """),
        {"season": season},
    ).scalars()
    # 2026 reuses bib numbers across conferences, so a rider is (category, bib, name).
    best: dict[tuple[str, int, str], tuple[TTCRow, int]] = {}
    lists = 0
    for rr_id in events:
        try:
            config = client.fetch_config(rr_id)
            names = season_list_names(config)
            # Some events also carry a "Top5" TTC list: keep the fullest one.
            rows: list[TTCRow] = []
            for name in names:
                found = parse_list(
                    client.fetch_results(config["server"], rr_id, config["key"], name)
                )
                if len(found) > len(rows):
                    rows = found
        except Exception:
            logger.exception("TTC: could not read raceresult event %s", rr_id)
            continue
        if not rows:
            continue
        lists += 1
        for r in rows:
            best.setdefault((r.category, r.bib, _norm(r.name)), (r, rr_id))

    # (category, bib, name) -> the rider who raced it that season; (category,
    # bib) alone only where one rider wore that number in the category.
    by_name: dict[tuple[str, int, str], tuple[int, str, str]] = {}
    by_bib: dict[tuple[str, int], set[tuple[int, str, str]]] = {}
    for cat, bib, name, rider_id, division, gender in session.execute(
        text("""
        SELECT DISTINCT r.category, r.bib, ri.name, r.rider_id, r.division, r.gender
        FROM results r
        JOIN events e ON e.id = r.event_id
        JOIN riders ri ON ri.id = r.rider_id
        WHERE e.season = :season AND r.bib IS NOT NULL
    """),
        {"season": season},
    ).all():
        by_name[(cat, bib, _norm(name))] = (rider_id, division, gender)
        by_bib.setdefault((cat, bib), set()).add((rider_id, division, gender))

    def match(cat: str, bib: int, name: str) -> tuple[int, str, str] | None:
        hit = by_name.get((cat, bib, name))
        if hit is None and len(by_bib.get((cat, bib), ())) == 1:
            hit = next(iter(by_bib[(cat, bib)]))
        return hit

    stored = unmatched = 0
    values = []
    seen: set[tuple[int, str, str]] = set()
    for (cat, bib, name), (row, rr_id) in best.items():
        hit = match(cat, bib, name)
        if hit is None:
            unmatched += 1
            continue
        rider_id, division, gender = hit
        if (rider_id, division, gender) in seen:
            continue
        seen.add((rider_id, division, gender))
        values.append(
            {
                "season": season,
                "rider_id": rider_id,
                "division": division,
                "gender": gender,
                "bib": bib,
                "bonus_points": row.bonus,
                "hours": row.hours,
                "raceresult_id": rr_id,
            }
        )
    if lists:  # never wipe a season because raceresult was unreachable
        session.execute(delete(ttc_bonus).where(ttc_bonus.c.season == season))
        if values:
            session.execute(ttc_bonus.insert(), values)
        stored = len(values)
    return {"lists": lists, "stored": stored, "unmatched": unmatched}
