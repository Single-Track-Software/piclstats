"""Race-day team sheet: one team, one upcoming race, every rider on one page.

Pure — no DB. The route feeds it the staging grids for the race (built the
way the league's row sheets are: division first, groups of 28, four rows of
seven) and the rating rows the forecast uses, and gets back one line per
rider on the team: where they stand on the line, where the ratings expect
them to finish, and about how long their race will take.

The time estimate is the one number here that is not already on another
page. A rider's rating is a log lap time relative to a typical rider on the
same loop; the day effect fitted to the *last event at this course* turns it
back into seconds, and the division's lap count at the course does the rest.
The first race at a new course has no such baseline, so the column is blank.
"""

from __future__ import annotations

import math

from piclstats.quality.keys import team_key
from piclstats.web import ratings

# Wave format the league stages each kind of race in (see staging.wave_joins).
STATE_FORMAT = "state"
CONFERENCE_FORMAT = "conference"


def _squash(value: str | None) -> str | None:
    return " ".join(value.split()) if value else None


def pick_race(races: list[dict], conference: str | None) -> dict | None:
    """The soonest race this team lines up at: a state race or its conference's.

    `races` are upcoming, soonest first. A team with no known conference sees
    the soonest race of any kind.
    """
    conference = _squash(conference)
    for race in races:
        race_conf = _squash(race.get("conference"))
        if race_conf is None or conference is None or race_conf == conference:
            return race
    return None


def team_races(races: list[dict], conference: str | None) -> list[dict]:
    """Every upcoming race the team lines up at, soonest first."""
    conference = _squash(conference)
    return [
        r
        for r in races
        if _squash(r.get("conference")) is None
        or conference is None
        or _squash(r.get("conference")) == conference
    ]


def team_conference(rows: list[dict], team: str, season: int) -> str | None:
    """The conference the team raced under in `season`, from its latest result."""
    key = team_key(team)
    latest: tuple | None = None
    conference = None
    for r in rows:
        if r["season"] != season or team_key(r.get("team")) != key:
            continue
        when = (r["event_order"] or 0, r["event_id"])
        if latest is None or when >= latest:
            latest = when
            conference = _squash(r.get("conference"))
    return conference


def staging_lines(grids: list[tuple[str, str, dict]], team: str) -> list[dict]:
    """The team's riders from each category grid, in sheet order.

    Unlike `staging.build_sheet` this keeps unrated riders: a coach wants to
    see every kid, and "no group yet" is the useful thing to know about one.

    A rider appears once. The grids overlap on purpose — last season's middle
    schoolers are carried on the MS grid until a race says otherwise — so a
    kid who has raced high school this season would otherwise show up twice.
    The grid where they have a race this season wins, else the first grid.
    """
    key = team_key(team)
    out = []
    seen: dict[int, int] = {}  # canonical_id -> index in out
    for age_group, gender, grid in grids:
        by_division: dict[str, int] = {}
        for r in grid["riders"]:
            by_division[r["division"]] = by_division.get(r["division"], 0) + 1
        for r in grid["riders"]:
            if team_key(r.get("team")) != key:
                continue
            cid = r["canonical_id"]
            if cid in seen:
                if r["basis"] == "current" and out[seen[cid]]["basis"] != "current":
                    out[seen[cid]]["_drop"] = True
                else:
                    continue
            seen[cid] = len(out)
            out.append(
                {
                    "age_group": age_group,
                    "gender": gender,
                    "canonical_id": r["canonical_id"],
                    "plate": r["plate"] or "",
                    "name": r["name"],
                    "division": r["division"] or "",
                    "category": f"{r['division']} - {'Boys' if gender == 'Male' else 'Girls'}",
                    "wave": r["wave"],
                    "group": r["group"],
                    "color": r["color"],
                    "color_css": r["color_css"],
                    "row": r["row"],
                    "rank": r["rank"],
                    "division_size": by_division.get(r["division"], 0),
                    "basis": r["basis"],
                    "division_assumed": r["division_assumed"],
                }
            )
    return [line for line in out if not line.get("_drop")]


def course_baselines(rows: list[dict], config: dict | None = None) -> dict[tuple, float]:
    """{(course_id, loop_type, gender): day effect of the latest event there}.

    The day effect is the log lap time of a typical rider on that loop that
    day (see `ratings.fit_event_effects`), so `exp(rating + effect)` is a lap
    in seconds at that course.
    """
    _, effects = ratings.fit_event_effects(rows, config)
    when: dict[int, tuple] = {}
    course: dict[int, int | None] = {}
    for r in rows:
        when[r["event_id"]] = (r["season"], r["event_order"] or 0)
        course[r["event_id"]] = r.get("course_id")
    latest: dict[tuple, tuple] = {}
    for (event_id, loop_type, gender), effect in effects.items():
        course_id = course.get(event_id)
        if course_id is None:
            continue
        key = (course_id, loop_type, gender)
        if key not in latest or when[event_id] > latest[key][0]:
            latest[key] = (when[event_id], effect)
    return {key: effect for key, (_, effect) in latest.items()}


def _minutes(seconds: float) -> int:
    return int(round(seconds / 60))


def team_forecasts(
    rows: list[dict],
    team: str,
    season: int,
    race: dict,
    laps_by_gender: dict[str, dict[str, int]],
    place_color,
    config: dict | None = None,
) -> dict[int, dict]:
    """{rider_id: forecast} for every rider on the team with a race this season.

    `rows` are `queries.rating_rows` for every loop and gender (oldest first);
    `race` has `conference` (None = state) and `course_id`; `laps_by_gender`
    is {gender: {division: laps}} at that course. Each forecast has the place
    distribution against the riders expected in the division (own conference
    only for a conference race), and — when the course has been raced before
    on this loop — a finish-time estimate with its range.
    """
    key = team_key(team)
    scores = ratings.race_scores(rows, config)
    roster = ratings.build_roster(rows, scores, season, config=config)
    history = ratings.field_history(rows)
    baselines = course_baselines(rows, config)
    conference = _squash(race.get("conference"))

    latest: dict[int, dict] = {}
    for r in rows:
        if r["season"] == season:
            latest[r["rider_id"]] = r
    mine = {rider_id for rider_id, r in latest.items() if team_key(r.get("team")) == key}

    by_division: dict[str, list[dict]] = {}
    for entry in roster:
        by_division.setdefault(entry["division"], []).append(entry)

    out: dict[int, dict] = {}
    for entry in roster:
        rider_id = entry["rider_id"]
        if rider_id not in mine:
            continue
        division = entry["division"]
        field = [
            r["rating"]
            for r in by_division.get(division, [])
            if r["rider_id"] != rider_id
            and (conference is None or _squash(r.get("conference")) == conference)
        ]
        forecast: dict = {"rating": entry["rating"], "division": division}
        if field:
            cell = ratings.place_distribution(
                entry["rating"],
                field,
                0.0,
                ratings.expected_field_size(history, season, division, conference),
            )
            cell["color"] = place_color(cell["place"], cell["field"])
            forecast.update(cell)

        gender = latest[rider_id]["gender"]
        loop_type = latest[rider_id].get("loop_type")
        laps = (laps_by_gender.get(gender) or {}).get(division)
        forecast["laps"] = laps
        baseline = baselines.get((race.get("course_id"), loop_type, gender))
        if laps and baseline is not None and entry["rating"].races:
            rating = entry["rating"]
            lap = math.exp(rating.mean + baseline)
            forecast["est_minutes"] = _minutes(lap * laps)
            forecast["est_low"] = _minutes(math.exp(rating.mean - rating.sd + baseline) * laps)
            forecast["est_high"] = _minutes(math.exp(rating.mean + rating.sd + baseline) * laps)
            forecast["lap_minutes"] = round(lap / 60, 1)
        out[rider_id] = forecast
    return out


def merge_lines(lines: list[dict], forecasts: dict[int, dict]) -> list[dict]:
    """Attach each staging line's forecast (None when the rider has none)."""
    return [{**line, "forecast": forecasts.get(line["canonical_id"])} for line in lines]
