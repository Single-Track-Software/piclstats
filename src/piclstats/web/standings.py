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
        JOIN events e ON e.id = r.event_id AND e.is_published AND e.event_type = 'points'
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
    for season, conference, division, gender in combos:
        rows = queries.leaderboard(
            session,
            season,
            division,
            gender,
            "total_points",
            limit=None,
            conferences=[conference],
        )
        out.setdefault(season, []).append(
            {
                "conference": conference,
                "division": division,
                "gender": gender,
                "standing": standing_in(rank_by_total(rows), rider_id),
            }
        )
    return out


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
