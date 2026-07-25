"""Staging / speed-rating engine.

Computes how fast a rider is relative to their age-group + gender field, as a
z-score — PICL's staging metric. Two flavors per event:

  - z_lap:  raw average lap time (reproduces PICL's spreadsheet; only valid
            within an event, where everyone rides the same loop)
  - z_pace: course-normalized min/mile (comparable across courses and seasons)

Per-event z-scores are rolled up season-to-date into 'average' and 'best-of'
aggregates, which drive staging order. Lower pace = faster, so a fast rider has
a NEGATIVE z. For display we surface a 'speed rating' = -avg_z (higher = faster)
and a field percentile.

This module is pure (no DB/IO): it takes the per-event rows produced by
queries.rider_speed_rating() and summarizes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist, fmean

_NORM = NormalDist()
_AGE_GROUP_LABELS = {"MS": "Middle School", "HS": "High School"}


@dataclass(frozen=True)
class EventZScore:
    """One event's z-scores for a rider, vs their age-group + gender field."""

    event_name: str
    season: int
    event_order: int
    age_group: str | None  # 'MS' | 'HS'
    division: str | None
    gender: str | None
    z_lap: float | None  # PICL-exact (raw lap time)
    z_pace: float | None  # course-normalized (min/mile)
    lap_field: int  # field size used for z_lap
    pace_field: int  # field size used for z_pace


@dataclass(frozen=True)
class MetricSummary:
    """Season-to-date roll-up of one metric's per-event z-scores."""

    metric: str  # 'pace' | 'lap'
    avg_z: float | None  # mean per-event z (negative = fast)
    best_z: float | None  # most-negative per-event z (the rider's ceiling)
    latest_z: float | None  # most recent event's z (current form)
    events_used: int
    percentile: float | None  # field percentile from avg_z (higher = faster)
    rating: float | None  # -avg_z, so higher = faster (display-friendly)
    label: str  # plain-English summary


def percentile_faster(z: float) -> float:
    """Field percentile for a z-score (higher = faster). z<0 is fast.

    A rider at z=-1.5 sits above ~93% of the field.
    """
    return round((1.0 - _NORM.cdf(z)) * 100.0, 1)


def _label(avg_z: float | None, events: int) -> str:
    if avg_z is None or events == 0:
        return "Not enough timed races yet"
    pct = percentile_faster(avg_z)
    sd = round(abs(avg_z), 1)
    conf = "" if events >= 3 else f" ({events} race{'s' if events != 1 else ''} — low confidence)"
    if avg_z < 0:
        return f"{sd} SD faster than the field — top {max(1, round(100 - pct))}%{conf}"
    if avg_z > 0:
        return f"{sd} SD slower than the field — {round(pct)}th percentile{conf}"
    return f"Right at the field average{conf}"


def _summary(zs: list[float | None], latest: float | None, metric: str) -> MetricSummary:
    vals = [z for z in zs if z is not None]
    if not vals:
        return MetricSummary(metric, None, None, None, 0, None, None, _label(None, 0))
    avg = fmean(vals)
    best = min(vals)  # most negative = fastest
    return MetricSummary(
        metric=metric,
        avg_z=round(avg, 2),
        best_z=round(best, 2),
        latest_z=round(latest, 2) if latest is not None else None,
        events_used=len(vals),
        percentile=percentile_faster(avg),
        rating=round(-avg, 2),
        label=_label(avg, len(vals)),
    )


def _to_event(row: dict) -> EventZScore:
    def f(v):
        return float(v) if v is not None else None

    return EventZScore(
        event_name=row.get("event_name", ""),
        season=row.get("season", 0),
        event_order=row.get("event_order") or 0,
        age_group=row.get("age_group"),
        division=row.get("division"),
        gender=row.get("gender"),
        z_lap=f(row.get("z_lap")),
        z_pace=f(row.get("z_pace")),
        lap_field=row.get("lap_field") or 0,
        pace_field=row.get("pace_field") or 0,
    )


def build_speed_rating(rows: list[dict]) -> dict:
    """Build the speed-rating view model from per-event z-score rows.

    Rows must be ordered oldest→newest (season, event_order).
    Returns {pace, lap: MetricSummary, events: [EventZScore], age_group, ...}.
    """
    events = [_to_event(r) for r in rows]

    latest_pace = next((e.z_pace for e in reversed(events) if e.z_pace is not None), None)
    latest_lap = next((e.z_lap for e in reversed(events) if e.z_lap is not None), None)

    # Context: use the most recent event with a known age group (a rider who
    # moved MS→HS is described by where they race now).
    age_group = next((e.age_group for e in reversed(events) if e.age_group), None)
    gender = next((e.gender for e in reversed(events) if e.gender), None)

    return {
        "pace": _summary([e.z_pace for e in events], latest_pace, "pace"),
        "lap": _summary([e.z_lap for e in events], latest_lap, "lap"),
        "events": events,
        "age_group": age_group,
        "age_group_label": _AGE_GROUP_LABELS.get(age_group or "", age_group or ""),
        "gender": gender,
        "has_data": any(e.z_pace is not None or e.z_lap is not None for e in events),
    }


# ── Staging grid (the /staging page) ────────────────────────────────────


def build_grid(
    rows: list[dict],
    metric: str = "pace",
    sort: str = "best",
    division: str | None = None,
    conference: str | None = None,
    wave_size: int = 20,
) -> dict:
    """Build the staging grid from per-(rider, event) z-score rows.

    Pivots into one row per kid with a z column per race, plus Best-z and Avg-z.
    Ranks the category (most negative = fastest first); a division and/or
    conference filter narrows to a specific race's field, re-ranked and split
    into waves of `wave_size`. A conference filter value matches either the
    specific conference (e.g. 'Eastern Blue') or its group (e.g. 'Eastern' =
    Blue + Gold), so you can model pack size both split and combined. Riders
    with no usable z sort last with no wave.
    """
    zkey = "z_pace" if metric == "pace" else "z_lap"
    sort_key = "best_z" if sort == "best" else "avg_z"

    events: dict = {}
    riders: dict = {}
    for r in rows:
        eid = r["event_id"]
        if eid not in events:
            events[eid] = {
                "event_id": eid,
                "event_order": r.get("event_order") or 0,
                "event_name": r["event_name"],
            }
        cid = r["canonical_id"]
        rd = riders.setdefault(
            cid,
            {
                "canonical_id": cid,
                "name": r.get("name"),
                "team": r.get("team"),
                "division": None,
                "conference": None,
                "conference_group": None,
                "_last": -1,
                "per_event": {},
                "_zs": [],
            },
        )
        z = r.get(zkey)
        z = float(z) if z is not None else None
        rd["per_event"][eid] = z
        if z is not None:
            rd["_zs"].append(z)
        order = r.get("event_order") or 0
        if order >= rd["_last"]:
            rd["_last"] = order
            rd["division"] = r.get("division")
            rd["conference"] = r.get("conference")
            rd["conference_group"] = r.get("conference_group")

    event_list = sorted(events.values(), key=lambda e: e["event_order"])
    divisions = sorted({r["division"] for r in riders.values() if r["division"]})

    # Conference dropdown: every specific conference, plus any group that spans
    # more than one conference (e.g. 'Eastern' over Blue + Gold) so the combined
    # pack can be modeled too.
    conferences = sorted({r["conference"] for r in riders.values() if r["conference"]})
    group_confs: dict = {}
    for r in riders.values():
        if r["conference_group"] and r["conference"]:
            group_confs.setdefault(r["conference_group"], set()).add(r["conference"])
    conference_groups = sorted(g for g, cs in group_confs.items() if len(cs) > 1)

    grid = []
    for rd in riders.values():
        zs = rd.pop("_zs")
        rd.pop("_last")
        rd["best_z"] = round(min(zs), 2) if zs else None
        rd["avg_z"] = round(sum(zs) / len(zs), 2) if zs else None
        rd["n_events"] = len(zs)
        grid.append(rd)

    if division:
        grid = [r for r in grid if r["division"] == division]
    if conference:
        grid = [r for r in grid if conference in (r["conference"], r["conference_group"])]

    # Most negative (fastest) first; unrated riders last.
    grid.sort(key=lambda r: (r[sort_key] is None, r[sort_key] if r[sort_key] is not None else 0.0))

    for i, r in enumerate(grid):
        r["rank"] = i + 1
        r["wave"] = (i // wave_size) + 1 if r[sort_key] is not None else None

    return {
        "events": event_list,
        "riders": grid,
        "divisions": divisions,
        "conferences": conferences,
        "conference_groups": conference_groups,
        "metric": metric,
        "sort": sort,
        "wave_size": wave_size,
        "rated_count": sum(1 for r in grid if r[sort_key] is not None),
    }
