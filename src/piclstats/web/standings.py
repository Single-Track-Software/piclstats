"""Conference standings: one ranked table per division and gender.

PICL standings are per conference (the state series only has the final's
day-of results), ranked by total points. Divisions run oldest first
(staging.DIVISION_ORDER: Varsity ... 6th Grade), Male before Female as in
the gender filter; riders level on points share a rank.
"""

from __future__ import annotations

from typing import Any

from piclstats.web.staging import division_sort_key

GENDER_ORDER = ("Male", "Female")


def _gender_key(gender: str | None) -> int:
    return GENDER_ORDER.index(gender) if gender in GENDER_ORDER else len(GENDER_ORDER)


def rank_by_total(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows sorted by total points (high first, then name) with a competition
    rank: equal totals share a rank and the next rank skips (1, 2, 2, 4)."""
    ordered = sorted(rows, key=lambda r: (-(r.get("total_points") or 0), r.get("name") or ""))
    out: list[dict[str, Any]] = []
    prev_total: object = object()
    rank = 0
    for i, row in enumerate(ordered, start=1):
        total = row.get("total_points") or 0
        if total != prev_total:
            rank, prev_total = i, total
        out.append({**row, "rank": rank})
    return out


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
