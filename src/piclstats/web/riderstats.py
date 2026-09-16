"""Per-season summaries for a rider, computed from their race rows.

Pure so the arithmetic is unit-tested; the SQL only has to deliver one row
per race with place, points, status, percentile, pct_behind, min_per_mile.
"""

from __future__ import annotations

from typing import Any


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def season_summary(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One dict per season, oldest first.

    Only scoring races with a place and clean data feed the averages; DNFs
    are counted separately. The division shown is the one from the rider's
    last placed race of the season, with a flag when they moved mid-season.
    """
    by_season: dict[int, list[dict[str, Any]]] = {}
    for r in races:
        by_season.setdefault(int(r["season"]), []).append(r)

    out: list[dict[str, Any]] = []
    for season in sorted(by_season):
        rows = by_season[season]
        scoring = [r for r in rows if r.get("event_type", "points") == "points"]
        placed = [r for r in scoring if r.get("place") and r.get("dq_status", "ok") != "excluded"]
        placed.sort(key=lambda r: (r.get("event_order") or 0))
        dnfs = sum(1 for r in scoring if (r.get("status") or "") in ("DNF", "DSQ", "DQ"))
        pct = [float(r["percentile"]) for r in placed if r.get("percentile") is not None]
        behind = [float(r["pct_behind"]) for r in placed if r.get("pct_behind") is not None]
        pace = [float(r["min_per_mile"]) for r in placed if r.get("min_per_mile") is not None]
        points = [int(r["points"]) for r in placed if r.get("points") is not None]
        divisions = [r["division"] for r in placed if r.get("division")]
        out.append(
            {
                "season": season,
                "races": len(placed),
                "dnfs": dnfs,
                "primary_division": divisions[-1] if divisions else None,
                "division_changed": len(set(divisions)) > 1,
                "avg_percentile": _avg(pct),
                "avg_pct_behind": _avg(behind),
                "best_pct_behind": round(min(behind), 1) if behind else None,
                "avg_pace": _avg(pace),
                "best_pace": round(min(pace), 1) if pace else None,
                "avg_points": _avg([float(p) for p in points]),
                "total_points": sum(points) if points else None,
                "best_place": min(int(r["place"]) for r in placed) if placed else None,
            }
        )
    return out
