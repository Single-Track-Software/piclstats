"""Per-season summaries for a rider, computed from their race rows.

Pure so the arithmetic is unit-tested; the SQL only has to deliver one row
per race with place, points, status, percentile, pct_behind, min_per_mile.
"""

from __future__ import annotations

from typing import Any


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _clean(r: dict[str, Any]) -> bool:
    """Timing data that passed the quality gate (laps and times are usable)."""
    return r.get("dq_status", "ok") != "excluded"


def _scoring(r: dict[str, Any]) -> bool:
    return r.get("event_type", "points") == "points"


def _placed(r: dict[str, Any]) -> bool:
    return bool(r.get("place")) and _scoring(r) and _clean(r)


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
        laps = sum(int(r.get("laps_ridden") or 0) for r in scoring if _clean(r))
        out.append(
            {
                "season": season,
                "races": len(placed),
                "dnfs": dnfs,
                "laps": laps,
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


# ── Career highlights ───────────────────────────────────────────────────

# Milestone race counts worth a line on the page.
RACE_MILESTONES = (10, 25, 50)
LAP_MILESTONES = (25, 50, 100, 200)


def _when(r: dict[str, Any]) -> str:
    return f"{r.get('event_name') or 'a race'} ({r['season']})"


def _lap_str(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def career_highlights(races: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Personal bests and milestones across every season, oldest race first.

    Counts (wins, podiums, laps, miles) use scoring races with clean data;
    laps and miles also count the laps before a DNF, which were still
    ridden. Rallies and other non-scoring events are left out entirely: the
    lap columns there hold segment times, not laps. Bests carry the race
    they were set at. Returns None when the rider has no usable race.
    """
    rows = sorted(races, key=lambda r: (int(r["season"]), r.get("event_order") or 0))
    placed = [r for r in rows if _placed(r)]
    clean = [r for r in rows if _clean(r) and _scoring(r)]
    if not placed and not clean:
        return None

    laps = sum(int(r.get("laps_ridden") or 0) for r in clean)
    miles = sum(
        int(r.get("laps_ridden") or 0) * float(r["loop_distance"])
        for r in clean
        if r.get("loop_distance")
    )

    def best(key, rows_):
        rows_ = [r for r in rows_ if r.get(key) is not None]
        return min(rows_, key=lambda r: float(r[key])) if rows_ else None

    def best_race(r, key):
        if r is None:
            return None
        return {
            "value": r[key],
            "event_name": r.get("event_name"),
            "season": int(r["season"]),
            "course": r.get("course"),
            "course_id": r.get("course_id"),
            "division": r.get("division"),
            "place": r.get("place"),
            "field_size": r.get("field_size"),
        }

    # Best finish: lowest place, then the biggest field it was done in.
    finish = (
        min(placed, key=lambda r: (int(r["place"]), -int(r.get("field_size") or 0)))
        if placed
        else None
    )
    fastest = best("best_lap_secs", clean)
    fastest_lap = best_race(fastest, "best_lap_secs")
    if fastest_lap:
        fastest_lap["text"] = _lap_str(float(fastest["best_lap_secs"]))
    # Fastest lap per course, for the "your best here" line on each venue.
    per_course: dict[int, dict[str, Any]] = {}
    for r in clean:
        if r.get("course_id") is None or r.get("best_lap_secs") is None:
            continue
        cur = per_course.get(r["course_id"])
        if cur is None or float(r["best_lap_secs"]) < float(cur["best_lap_secs"]):
            per_course[r["course_id"]] = r
    course_bests = {
        cid: {"text": _lap_str(float(r["best_lap_secs"])), "season": int(r["season"]),
              "event_name": r.get("event_name")}
        for cid, r in per_course.items()
    }  # fmt: skip

    wins = [r for r in placed if int(r["place"]) == 1]
    podiums = [r for r in placed if int(r["place"]) <= 3]
    top10 = [r for r in placed if int(r["place"]) <= 10]

    # First podium and first win, in the order they happened; one line when
    # the first podium was the win itself.
    milestones: list[dict[str, Any]] = []
    if podiums and not (wins and podiums[0] is wins[0]):
        milestones.append({"label": "First podium", "when": _when(podiums[0])})
    if wins:
        milestones.append({"label": "First win", "when": _when(wins[0])})
    for n in RACE_MILESTONES:
        if len(placed) >= n:
            milestones.append({"label": f"{n}th race", "when": _when(placed[n - 1])})
    running = 0
    for r in clean:
        before = running
        running += int(r.get("laps_ridden") or 0)
        for n in LAP_MILESTONES:
            if before < n <= running:
                milestones.append({"label": f"{n} laps", "when": _when(r)})

    return {
        "races": len(placed),
        "seasons": len({int(r["season"]) for r in placed}),
        "laps": laps,
        "miles": round(miles) if miles else None,
        "wins": len(wins),
        "podiums": len(podiums),
        "top10": len(top10),
        "best_finish": best_race(finish, "place"),
        "best_percentile": best_race(
            max(
                (r for r in placed if r.get("percentile") is not None),
                key=lambda r: float(r["percentile"]),
                default=None,
            ),
            "percentile",
        ),
        "fastest_lap": fastest_lap,
        "best_pace": best_race(best("min_per_mile", placed), "min_per_mile"),
        "course_bests": course_bests,
        "milestones": milestones,
    }
