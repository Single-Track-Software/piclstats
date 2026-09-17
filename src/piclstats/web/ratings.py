"""Relative speed ratings and the future-race place forecast (pure — no DB).

The cross-division forecast in `forecast.py` compares min/mile, which leans on
loop distances that are still league defaults at every course. This module
never touches distance. A rider's *score* for a race is

    log(average lap time) - how slow that day was for everyone on the same
        loop, same gender (an event effect fitted from who was in the field)

so the course, the weather and the loop length all cancel: -0.10 means "about
10% faster than the typical rider", wherever it was. A *rating* is a
recency-weighted mean of recent scores with an honest spread, and a forecast
ranks the rider's rating against the ratings of the riders expected to line
up — the division's current roster, cut to one conference for a conference
race — integrating over everyone having a good or bad day.

Parameters were chosen by replaying 2024-26 (`piclstats forecast-backtest`).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import NormalDist, median, pstdev

_NORMAL = NormalDist()

RATING_CONFIG: dict = {
    # Rating = weighted mean of the last N scores, newest weight 1, then
    # decay, decay^2 ... Kids change fast: 0.6 beat 0.8 and any long average.
    "recent_race_count": 5,
    "recency_decay": 0.6,
    # Race-to-race spread of a rider's score around their rating (log units,
    # so 0.065 is about 6.5% of lap time) once they have raced this season...
    "race_sd": 0.065,
    # ...and when the newest score is from an earlier season: a winter of
    # growth makes last year a much rougher guide.
    "stale_race_sd": 0.10,
    # Extra down-weighting of any score from an earlier season, for the same
    # reason: once a rider has raced this year, last year barely counts.
    "season_decay": 0.25,
    # A same-day field smaller than this is too thin to take a median from.
    "min_field": 8,
}

# Target-performance quantiles the place distribution is integrated over.
_GRID = [_NORMAL.inv_cdf((k + 0.5) / 21) for k in range(21)]


@dataclass(frozen=True)
class Score:
    """One race, relative to that day's same-loop, same-gender field."""

    event_id: int
    season: int
    event_order: int
    division: str
    x: float  # log lap time less the event effect; negative = faster than typical


@dataclass(frozen=True)
class Rating:
    mean: float
    sd: float  # spread of the rider's *next race* around `mean`
    races: int
    stale: bool  # newest score is from before the forecast season


def race_scores(rows: list[dict], config: dict | None = None) -> dict[int, list[Score]]:
    """Scores per rider, oldest first.

    `rows` are placed results with event_id, season, event_order, rider_id,
    division, gender, loop_type, laps and lap_secs (ride time / laps). Only
    riders who went the full distance for their division score: someone pulled
    at the cutoff after fewer laps has a flattering average lap.
    """
    cfg = {**RATING_CONFIG, **(config or {})}
    full_laps: dict[tuple, int] = defaultdict(int)
    for r in rows:
        key = (r["event_id"], r["division"], r["gender"])
        full_laps[key] = max(full_laps[key], r["laps"] or 0)

    fields: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if not r.get("lap_secs") or not r.get("loop_type") or not r["laps"]:
            continue
        if r["laps"] == full_laps[(r["event_id"], r["division"], r["gender"])]:
            fields[(r["event_id"], r["loop_type"], r["gender"])].append(r)

    fields = {k: v for k, v in fields.items() if len(v) >= cfg["min_field"]}
    baseline = _event_effects(fields)
    scores: dict[int, list[Score]] = defaultdict(list)
    for key, members in fields.items():
        for m in members:
            scores[m["rider_id"]].append(
                Score(
                    event_id=m["event_id"],
                    season=m["season"],
                    event_order=m["event_order"] or 0,
                    division=m["division"],
                    x=math.log(float(m["lap_secs"])) - baseline[key],
                )
            )
    for history in scores.values():
        history.sort(key=lambda s: (s.season, s.event_order, s.event_id))
    return dict(scores)


def _field_medians(fields: dict[tuple, list[dict]]) -> dict[tuple, float]:
    return {k: median(math.log(float(m["lap_secs"])) for m in v) for k, v in fields.items()}


def _event_effects(fields: dict[tuple, list[dict]], rounds: int = 12) -> dict[tuple, float]:
    """How slow each field's day was, judged by who was in it (two-way fit).

    A field's median only measures the course if the same sort of riders turn
    up everywhere. They don't: a conference race draws one region, and its
    median rider may be stronger or weaker than the league's. So fit
    log(lap) = rider ability (per season) + event effect by alternating means —
    riders who race both state and conference events tie the fields together.
    """
    effect = _field_medians(fields)
    for _ in range(rounds):
        ability: dict[tuple, list[float]] = defaultdict(list)
        for key, members in fields.items():
            for m in members:
                ability[(m["rider_id"], m["season"], key[1], key[2])].append(
                    math.log(float(m["lap_secs"])) - effect[key]
                )
        mean_ability = {k: sum(v) / len(v) for k, v in ability.items()}
        for key, members in fields.items():
            resid = [
                math.log(float(m["lap_secs"]))
                - mean_ability[(m["rider_id"], m["season"], key[1], key[2])]
                for m in members
            ]
            effect[key] = sum(resid) / len(resid)
    return effect


def rate(scores: list[Score], season: int, config: dict | None = None) -> Rating | None:
    """Rating going into a race in `season`, from scores oldest-first."""
    if not scores:
        return None
    cfg = {**RATING_CONFIG, **(config or {})}
    recent = scores[-max(1, int(cfg["recent_race_count"])) :]
    weights = [cfg["recency_decay"] ** (len(recent) - 1 - i) for i in range(len(recent))]
    old = [s.season < season for s in recent]
    weights = [w * (cfg["season_decay"] if o else 1.0) for w, o in zip(weights, old)]
    total = sum(weights)
    mean = sum(s.x * w for s, w in zip(recent, weights)) / total

    stale = old[-1]
    race_sd = cfg["stale_race_sd"] if stale else cfg["race_sd"]
    # Uncertainty in the rating itself (few races -> wide) on top of race-day noise.
    rating_var = race_sd**2 * sum(w * w for w in weights) / total**2
    return Rating(mean=mean, sd=math.sqrt(race_sd**2 + rating_var), races=len(recent), stale=stale)


def place_distribution(
    target: Rating,
    field: list[Rating],
    shift: float = 0.0,
    expected_field: int | None = None,
) -> dict:
    """Median place and 80% range for `target` against the known `field`.

    `shift` moves the target's score (log units) — fatigue for extra laps. Each
    rival beats the target with the normal probability their day is better.
    The target's own day is the big shared term (a bad one drops them behind
    everybody at once), so the distribution is a mixture over a grid of target
    performances rather than one normal approximation. Deterministic: no draws.

    The rivals we can rate are never exactly who starts — riders skip races,
    and early in a season many haven't appeared yet — so what is forecast is
    the *share of the field ahead*, scaled to `expected_field` (see
    `expected_field_size`; defaults to the known rivals plus the rider).
    """
    n = len(field)
    size = max(2, expected_field or n + 1)
    scale = (size - 1) / max(n, 1)
    components: list[tuple[float, float]] = []
    for z in _GRID:
        y = target.mean + shift + target.sd * z
        mu = var = 0.0
        for rider in field:
            p = _NORMAL.cdf((y - rider.mean) / rider.sd)
            mu += p
            var += p * (1 - p)
        components.append((mu * scale, math.sqrt(var) * scale))

    def cdf(place: int) -> float:  # P(finish at `place` or better)
        ahead = place - 1
        total = 0.0
        for mu, sd in components:
            total += _NORMAL.cdf((ahead + 0.5 - mu) / sd) if sd > 0 else float(ahead + 0.5 >= mu)
        return total / len(components)

    def quantile(q: float) -> int:
        for place in range(1, size + 1):
            if cdf(place) >= q:
                return place
        return size

    return {
        "place": quantile(0.5),
        "place_low": quantile(0.1),
        "place_high": quantile(0.9),
        "field": size,
    }


def field_history(rows: list[dict]) -> list[dict]:
    """Field size of every (event, division) in `rows`, oldest first.

    `conference` is the one conference an event drew, or None for a state race.
    """
    sizes: dict[tuple, int] = defaultdict(int)
    meta: dict[int, tuple] = {}
    conferences: dict[int, set] = defaultdict(set)
    for r in rows:
        sizes[(r["event_id"], r["division"])] += 1
        meta[r["event_id"]] = (r["season"], r["event_order"] or 0)
        if r.get("conference"):
            conferences[r["event_id"]].add(_squash(r["conference"]))
    history = [
        {
            "event_id": event_id,
            "season": meta[event_id][0],
            "event_order": meta[event_id][1],
            "division": division,
            "conference": next(iter(conferences[event_id]))
            if len(conferences[event_id]) == 1
            else None,
            "size": size,
        }
        for (event_id, division), size in sizes.items()
    ]
    history.sort(key=lambda h: (h["season"], h["event_order"], h["event_id"]))
    return history


def expected_field_size(
    history: list[dict], season: int, division: str, conference: str | None
) -> int | None:
    """How many will start: the latest like-for-like field, else last season's average.

    Like-for-like = same division and same draw (state, or that conference).
    None when there is nothing comparable, leaving the caller to fall back to
    the riders it knows about.
    """
    conference = _squash(conference)
    alike = [h for h in history if h["division"] == division and h["conference"] == conference]
    this_season = [h["size"] for h in alike if h["season"] == season]
    if this_season:
        return this_season[-1]
    earlier = [h for h in alike if h["season"] < season]
    if earlier:
        last = max(h["season"] for h in earlier)
        sizes = [h["size"] for h in earlier if h["season"] == last]
        return round(sum(sizes) / len(sizes))
    return None


def fatigue_shift(own_laps: int | None, target_laps: int | None, fatigue_per_lap: float) -> float:
    """Score penalty for a division that rides more laps; never a bonus for fewer."""
    if not own_laps or not target_laps:
        return 0.0
    return math.log(1.0 + fatigue_per_lap * max(0, target_laps - own_laps))


def _squash(value: str | None) -> str | None:
    return " ".join(value.split()) if value else None


def build_roster(
    prior_rows: list[dict],
    scores: dict[int, list[Score]],
    season: int,
    before: tuple[int, int] | None = None,
    config: dict | None = None,
) -> list[dict]:
    """Everyone seen in `season` so far, in their latest division, with a rating.

    `prior_rows` (oldest first) must already be limited to results before the
    race being forecast; `before` = (season, event_order) trims each rider's
    scores to match (None = use them all). A rider with no usable score yet
    gets their division's median rating with the division's spread added: they
    still take up a place in the field, we just don't know which.
    """
    cfg = {**RATING_CONFIG, **(config or {})}
    latest: dict[int, dict] = {}
    for r in prior_rows:
        if r["season"] == season:
            latest[r["rider_id"]] = r

    roster = []
    for rider_id, r in latest.items():
        history = scores.get(rider_id, [])
        if before is not None:
            history = [s for s in history if (s.season, s.event_order) < before]
        roster.append(
            {
                "rider_id": rider_id,
                "division": r["division"],
                "conference": _squash(r.get("conference")),
                "order": r.get("category_order"),
                "rating": rate(history, season, cfg),
            }
        )

    by_division: dict[str, list[float]] = defaultdict(list)
    for entry in roster:
        if entry["rating"] is not None:
            by_division[entry["division"]].append(entry["rating"].mean)
    for entry in roster:
        if entry["rating"] is None:
            known = by_division.get(entry["division"]) or [0.0]
            spread = pstdev(known) if len(known) > 1 else 0.14
            entry["rating"] = Rating(
                mean=median(known),
                sd=math.sqrt(cfg["race_sd"] ** 2 + spread**2),
                races=0,
                stale=False,
            )
    return roster


def build_future_matrix(
    rider_id: int,
    rider_division: str,
    roster: list[dict],
    races: list[dict],
    place_color,
    fatigue_per_lap: float,
    history: list[dict],
    season: int,
) -> dict | None:
    """Upcoming races × divisions: where the rider is forecast to finish.

    `roster` is everyone expected to race the rider's loop this season —
    rider_id, division, conference, rating (a `Rating`) — including the rider.
    `history` is `field_history()` of the loaded results, for field sizes.
    `races` are upcoming scheduled races, soonest first, each with name,
    event_date, course, conference (None = state) and `laps`, a
    {division: lap count} for that course. `place_color(place, field)` is the
    shared green/amber/red rule from `forecast.py`.
    """
    me = next((r for r in roster if r["rider_id"] == rider_id), None)
    if me is None or not races:
        return None

    by_division: dict[str, list[dict]] = defaultdict(list)
    for r in roster:
        if r["rider_id"] != rider_id:
            by_division[r["division"]].append(r)

    order = {r["division"]: r["order"] for r in roster if r.get("order") is not None}
    rows = []
    seen: list[str] = []
    for race in races:
        conference = _squash(race.get("conference"))
        laps = race.get("laps") or {}
        cells: dict[str, dict] = {}
        for division, riders in by_division.items():
            field = [
                r["rating"]
                for r in riders
                if conference is None or _squash(r.get("conference")) == conference
            ]
            if not field:
                continue
            shift = fatigue_shift(laps.get(rider_division), laps.get(division), fatigue_per_lap)
            cell = place_distribution(
                me["rating"],
                field,
                shift,
                expected_field_size(history, season, division, conference),
            )
            cell["color"] = place_color(cell["place"], cell["field"])
            cell["own"] = division == rider_division
            cell["extra_laps"] = max(0, (laps.get(division) or 0) - (laps.get(rider_division) or 0))
            cells[division] = cell
            if division not in seen:
                seen.append(division)
        rows.append({"race": race, "cells": cells})

    seen.sort(key=lambda d: (order.get(d, 10**6), d))
    return {"divisions": seen, "rows": rows, "rating": me["rating"]}
