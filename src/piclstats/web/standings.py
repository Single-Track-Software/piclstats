"""Conference standings: one ranked table per division and gender.

PICL standings are per conference (the state series only has the final's
day-of results), ranked by total points. Divisions run oldest first
(staging.DIVISION_ORDER: Varsity ... 6th Grade), Male before Female as in
the gender filter; riders level on points share a rank.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from piclstats.web.staging import division_sort_key

GENDER_ORDER = ("Male", "Female")


def _gender_key(gender: str | None) -> int:
    return GENDER_ORDER.index(gender) if gender in GENDER_ORDER else len(GENDER_ORDER)


# Above any race's points (Varsity 1st = 575).
_MAX_POINTS = 600


def champion_tie_key(row: dict[str, Any]) -> tuple:
    """Handbook 5.15 tie-break for the conference series champion: most
    conference wins, then most 2nds, 3rds ... (every series race counts,
    dropped or not), then the better finish in the last series race.

    Finishes are read from points, not places: at a state race the points
    follow the rider's place among their own conference (13th overall can be
    575, a conference win), and within one category more points is always
    the better finish.
    """
    points = row.get("race_points") or {}
    races = row.get("series_races") or []
    tally = Counter(points.values())
    # Fixed length so riders compare finish by finish: (-wins, -2nds, ...).
    counts = tuple(-tally[p] for p in range(_MAX_POINTS, -1, -1))
    last = points.get(races[-1]) if races else None
    return (*counts, -(last or 0))


def rank_by_total(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows sorted by total points (high first, then name) with a competition
    rank: equal totals share a rank and the next rank skips (1, 2, 2, 4).

    The one exception is a tie for 1st, which the handbook breaks
    (`champion_tie_key`) when the rows carry race points. Other ties stay
    shared, as in the league's own series lists.
    """
    ordered = sorted(rows, key=lambda r: (-(r.get("total_points") or 0), r.get("name") or ""))
    top = (
        [
            r
            for r in ordered
            if (r.get("total_points") or 0) == (ordered[0].get("total_points") or 0)
        ]
        if ordered
        else []
    )
    if len(top) > 1 and all("race_points" in r for r in top):
        top.sort(key=champion_tie_key)
        ordered = top + ordered[len(top) :]
    out: list[dict[str, Any]] = []
    prev: object = object()
    rank = 0
    for i, row in enumerate(ordered, start=1):
        total = row.get("total_points") or 0
        level = (total, champion_tie_key(row)) if i <= len(top) and len(top) > 1 else (total,)
        if level != prev:
            rank, prev = i, level
        out.append({**row, "rank": rank})
    return out


# Handbook 5.15: a conference series of this many races or more drops each
# rider's lowest race (best 3 of 4); a missed race is the score dropped.
DROP_FROM_RACES = 4


def series_total(scores: list[int], series_races: int) -> int:
    """A rider's conference series score from their race scores in one category.

    `scores` = points for each series race they rode (a DNF scores its points;
    races they missed are absent); `series_races` = races the conference series
    actually held. Checked against raceresult's own series lists for 2024-26:
    4 races held -> best 3 of 4, 3 held (2026, a race cancelled) -> no drop.
    """
    total = sum(scores)
    if series_races >= DROP_FROM_RACES and scores and len(scores) >= series_races:
        total -= min(scores)
    return total


# `series_score`'s dropped value when the dropped "race" is one they missed.
MISSED = "missed"


def series_score(points: dict[int, int], races: list[int]) -> tuple[int, int | str | None]:
    """(series score, what was dropped) for one rider in one category.

    `points` = event id -> points for the series races they rode; `races` =
    the conference's series races held, oldest first. Dropped is the event id
    of the lowest race (ties: the earlier one, as raceresult drops it), MISSED
    when a missed race is the drop, or None when nothing is (under four races).
    """
    total = sum(points.values())
    if len(races) < DROP_FROM_RACES or not points:
        return total, None
    ridden = [e for e in races if e in points]
    if len(ridden) < len(races):
        return total, MISSED
    low = min(ridden, key=lambda e: points[e])
    return total - points[low], low


def group_standings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split leaderboard rows into ranked tables per (division, gender), oldest division first."""
    groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row.get("division"), row.get("gender")), []).append(row)
    keys = sorted(groups, key=lambda k: (division_sort_key(k[0]), _gender_key(k[1])))
    return [
        {"division": division, "gender": gender, "rows": rank_by_total(groups[(division, gender)])}
        for division, gender in keys
    ]


def ordinal(n: int) -> str:
    """1 -> 1st, 2 -> 2nd, 11 -> 11th, 23 -> 23rd."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def standing_in(ranked: list[dict[str, Any]], rider_id: int) -> dict[str, Any] | None:
    """The rider's place in one ranked table: rank, field size, total, tie flag."""
    row = next((r for r in ranked if r.get("rider_id") == rider_id), None)
    if row is None:
        return None
    tied = sum(1 for r in ranked if r["rank"] == row["rank"]) > 1
    return {
        "rank": row["rank"],
        "label": ("T" if tied else "") + ordinal(row["rank"]),
        "of": len(ranked),
        "total_points": row.get("total_points") or 0,
    }


def rider_conference_standings(session: Any, rider_id: int) -> dict[int, list[dict[str, Any]]]:
    """Season -> the rider's conference standing(s), computed like the leaderboard.

    One entry per (conference, division, gender) the rider scored in that
    season; usually one, more if they moved division or team. `standing` is
    None when they haven't yet ridden enough races to rank (the leaderboard's
    two-race minimum).
    """
    from sqlalchemy import text

    from piclstats.web import queries

    combos = session.execute(
        text(r"""
        SELECT DISTINCT e.season, regexp_replace(tc.conference, '\s+', ' ', 'g') AS conference,
               r.division, r.gender
        FROM results r
        JOIN events e ON e.id = r.event_id AND e.is_published
            AND COALESCE(e.counts_in_series, e.event_type = 'points')
        JOIN riders ri ON ri.id = r.rider_id
        JOIN team_conferences tc ON tc.team = ri.team AND tc.season = e.season
        WHERE r.rider_id IN (
                SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
                UNION SELECT CAST(:cid AS int)
              )
          AND r.place IS NOT NULL AND r.dq_status <> 'excluded'
        ORDER BY e.season
    """),
        {"cid": rider_id},
    ).all()
    out: dict[int, list[dict[str, Any]]] = {}
    stamp = queries.results_stamp(session) if combos else ()
    for season, conference, division, gender in combos:
        ranked = _ranked_conference(session, stamp, season, conference, division, gender)
        out.setdefault(season, []).append(
            {
                "conference": conference,
                "division": division,
                "gender": gender,
                "standing": standing_in(ranked, rider_id),
            }
        )
    return out


# Ranked conference tables for rider pages, keyed by the results stamp so a
# load, republish or merge rebuilds them. The TTL covers edits the stamp
# doesn't see (DQ status, team conferences). Every rider page in a conference
# shares one table; rebuilding it per page made a crawl of rider pages
# (2026-10-23) run the full leaderboard query once per page.
_RANKED_TTL_S = 600
_ranked_cache: dict[tuple, tuple[tuple, float, list[dict[str, Any]]]] = {}


def _ranked_conference(
    session: Any, stamp: tuple, season: int, conference: str, division: str, gender: str
) -> list[dict[str, Any]]:
    from piclstats.web import queries

    key = (season, conference, division, gender)
    hit = _ranked_cache.get(key)
    now = time.monotonic()
    if hit and hit[0] == stamp and now - hit[1] < _RANKED_TTL_S:
        return hit[2]
    rows = queries.leaderboard(
        session,
        season,
        division,
        gender,
        "total_points",
        limit=None,
        conferences=[conference],
    )
    ranked = rank_by_total(rows)
    _ranked_cache[key] = (stamp, now, ranked)
    return ranked


def division_conference_matrix(
    rows: list[dict[str, Any]], conferences: list[str]
) -> dict[str, Any]:
    """Shape (division, conference, riders) rows for the dashboard's stacked bars.

    Divisions run oldest first; conferences in the order given (west to
    east), with any extra conference, and riders with none ("Unassigned"),
    after them. Returns divisions, conferences, counts[conference][i], totals[i].
    """
    divisions = sorted({r["division"] for r in rows}, key=division_sort_key)
    extra = sorted({r["conference"] or "Unassigned" for r in rows} - set(conferences))
    confs = [c for c in conferences if any((r["conference"] or "") == c for r in rows)] + extra
    counts = {c: [0] * len(divisions) for c in confs}
    for r in rows:
        counts[r["conference"] or "Unassigned"][divisions.index(r["division"])] += r["riders"]
    totals = [sum(counts[c][i] for c in confs) for i in range(len(divisions))]
    return {"divisions": divisions, "conferences": confs, "counts": counts, "totals": totals}
