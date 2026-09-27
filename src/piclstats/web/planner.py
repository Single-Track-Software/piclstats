"""Race duration planner: will each category finish inside its window? (pure)

Organisers set lap counts per division by feel. This turns the ratings of
the riders expected in each division into a finish-time spread at a course
— winner, median, last rider — and holds it against the division's time
window (`max_duration_mins`) and lap cutoff (`cutoff_mins`), so a lap count
can be tried before it is printed on the flyer.

A rider's rating is a log lap time relative to a typical rider on the loop;
the day effect of the last race on that loop at the course turns it into
seconds (see `raceday.course_baselines`). A course with no race on the
loop yet borrows the league-typical day effect and says so.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

from piclstats.web import ratings
from piclstats.web.raceday import course_baselines
from piclstats.web.staging import division_sort_key

# Last finisher this close to the window is "tight" rather than "ok".
TIGHT_SHARE = 0.9


def _squash(value: str | None) -> str | None:
    return " ".join(value.split()) if value else None


def _mins(seconds: float) -> float:
    return seconds / 60


def typical_baseline(baselines: dict[tuple, float], loop_type: str, gender: str) -> float | None:
    """Mean day effect over every course on a loop: the fallback for a new course."""
    values = [b for (_, lt, g), b in baselines.items() if lt == loop_type and g == gender]
    return sum(values) / len(values) if values else None


def plan_division(
    riders: list[ratings.Rating],
    laps: int,
    baseline: float,
    window_mins: int | None,
    cutoff_mins: int | None,
) -> dict[str, Any]:
    """Finish-time spread for one division at `laps` laps.

    Each rider's expected lap is exp(rating + day effect); the last finisher
    is judged on a slow day (rating + 1 sd) because the window has to hold on
    a bad day too. A rider still short of the last lap at the cutoff is
    pulled, and finishes on fewer laps, so they are counted rather than
    timed.
    """
    if not riders or laps <= 0:
        return {"laps": laps, "riders": len(riders)}
    laps_secs = [math.exp(r.mean + baseline) for r in riders]
    totals = sorted(lap * laps for lap in laps_secs)
    slow_totals = sorted(math.exp(r.mean + r.sd + baseline) * laps for r in riders)
    winner = _mins(totals[0])
    mid = _mins(median(totals))
    last = _mins(slow_totals[-1])
    # Pulled at the cutoff: not yet out on the final lap when the cutoff passes.
    pulled = 0
    if cutoff_mins and laps > 1:
        pulled = sum(1 for lap in laps_secs if _mins(lap * (laps - 1)) > cutoff_mins)
        finishers = sorted(
            math.exp(r.mean + r.sd + baseline) * laps
            for r in riders
            if _mins(math.exp(r.mean + baseline) * (laps - 1)) <= cutoff_mins
        )
        if finishers:
            last = _mins(finishers[-1])
    status = None
    if window_mins:
        if last > window_mins:
            status = "over"
        elif last > TIGHT_SHARE * window_mins:
            status = "tight"
        else:
            status = "ok"
    return {
        "laps": laps,
        "riders": len(riders),
        "lap_mins": round(_mins(median(laps_secs)), 1),
        "winner_mins": round(winner, 1),
        "median_mins": round(mid, 1),
        "last_mins": round(last, 1),
        "pulled": pulled,
        "window_mins": window_mins,
        "cutoff_mins": cutoff_mins,
        "status": status,
        "headroom_mins": int(round(window_mins - last)) if window_mins else None,
    }


def plan_course(
    rows_by_loop: dict[tuple[str, str], list[dict]],
    season: int,
    course_id: int | None,
    conference: str | None,
    profiles: list[dict[str, Any]],
    overrides: dict[tuple[str, str], int] | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Every division at a course: expected riders, finish spread, window check.

    `rows_by_loop` = {(gender, loop_type): queries.rating_rows(...)}, every
    season (the day effects need history; the roster is `season`'s riders).
    `profiles` = the course's division rows (division, gender, loop_type,
    lap_count, max_duration_mins, cutoff_mins). `overrides` = what-if lap
    counts by (division, gender). `conference` limits the field to one
    conference's riders (None = state race: everyone).
    """
    conference = _squash(conference)
    overrides = overrides or {}
    profile_by = {(p["division"], p["gender"]): p for p in profiles}
    by_loop_gender: dict[tuple[str, str], dict[str, list[ratings.Rating]]] = {}
    baselines_by: dict[tuple[str, str], dict[tuple, float]] = {}
    conferences: set[str] = set()
    for (gender, loop_type), rows in rows_by_loop.items():
        scores = ratings.race_scores(rows, config)
        roster = ratings.build_roster(rows, scores, season, config=config)
        by_division: dict[str, list[ratings.Rating]] = {}
        for entry in roster:
            if entry.get("conference"):
                conferences.add(entry["conference"])
            if conference and _squash(entry.get("conference")) != conference:
                continue
            by_division.setdefault(entry["division"], []).append(entry["rating"])
        by_loop_gender[(gender, loop_type)] = by_division
        baselines_by[(gender, loop_type)] = course_baselines(rows, config)

    divisions: list[dict[str, Any]] = []
    borrowed = False
    for (gender, loop_type), by_division in by_loop_gender.items():
        baselines = baselines_by[(gender, loop_type)]
        baseline = baselines.get((course_id, loop_type, gender)) if course_id else None
        source = "course"
        if baseline is None:
            baseline = typical_baseline(baselines, loop_type, gender)
            source = "typical"
            borrowed = True
        for division, riders in by_division.items():
            profile = profile_by.get((division, gender)) or profile_by.get((division, None))
            if profile is None or profile.get("loop_type") not in (None, loop_type):
                continue
            laps = overrides.get((division, gender), profile["lap_count"])
            row = {
                "division": division,
                "gender": gender,
                "loop_type": loop_type,
                "profile_laps": profile["lap_count"],
                "baseline_source": source,
            }
            if baseline is not None:
                row.update(
                    plan_division(
                        riders,
                        laps,
                        baseline,
                        profile.get("max_duration_mins"),
                        profile.get("cutoff_mins"),
                    )
                )
            else:
                row.update({"laps": laps, "riders": len(riders)})
            divisions.append(row)

    divisions.sort(key=lambda d: (d["gender"] != "Male", division_sort_key(d["division"])))
    return {
        "divisions": divisions,
        "conferences": sorted(conferences),
        "borrowed_baseline": borrowed,
        "season": season,
    }
