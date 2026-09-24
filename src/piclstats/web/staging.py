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


# Staging always runs down the ladder, fastest category first.
DIVISION_ORDER = [
    "Varsity",
    "JV1",
    "JV2",
    "JV3",
    "9th Grade",
    "MS Advanced",
    "Middle School Advanced",
    "8th Grade",
    "7th Grade",
    "6th Grade",
]

# PICL's staging vocabulary (from the league's "Staging Dots & Tape"
# instructions): a WAVE is the set of categories that roll off the line
# together (e.g. Varsity + JV1 Male); a GROUP is the start box inside the wave,
# marked by a coloured dot on the plate; a ROW is the line within the group,
# written on the dot. Groups are 28 riders = 4 rows of 7.
GROUP_SIZE = 28
ROW_SIZE = 7
GROUP_COLORS = ["Red", "Yellow", "Green", "Blue", "Orange", "Neon Pink", "Purple", "White"]
# Screen approximations of the dot colours, for the swatch beside a group.
GROUP_COLOR_CSS = {
    "Red": "#dc2626",
    "Yellow": "#facc15",
    "Green": "#16a34a",
    "Blue": "#2563eb",
    "Orange": "#f97316",
    "Neon Pink": "#ff2d95",
    "Purple": "#7e22ce",
    "White": "#ffffff",
}

# Wave formats: which divisions share a wave. A format names the divisions
# that ride *with the division above them*; "custom" comes from the page's
# checkboxes. The league's real groupings (2026 row sheets):
#   conference  HS boys {Varsity, JV1} {JV2, JV3}; HS girls, MS girls and
#               MS boys each all together (MS Advanced first)
#   state       MS boys {MS Advanced, 8th} then 7th and 6th on their own;
#               MS girls all together. (HS at state: no sheet seen — same as
#               a conference race until told otherwise.)
WAVE_FORMATS: dict[str, str] = {
    "conference": "Conference race",
    "state": "State race",
    "separate": "Each division on its own",
    "combined": "All divisions together",
    "custom": "Custom",
}
_HS_BOYS_CONFERENCE = {"JV1", "JV3"}
_MS_BOYS_STATE = {"8th Grade"}

# Where a rider staged on last season's data probably races this season, until
# they have a result (or a registration) that says. Grade divisions move up a
# year; an 8th grader is in high school now and most start in JV3. Skill-tier
# divisions and MS Advanced stay put — no better guess without registration.
PROMOTE_DIVISION = {
    "5th Grade": "6th Grade",
    "6th Grade": "7th Grade",
    "7th Grade": "8th Grade",
    "8th Grade": "JV3",
}
HS_DIVISIONS = {"Varsity", "JV1", "JV2", "JV3"}


def division_age_group(division: str | None) -> str | None:
    """'HS' or 'MS' for a division name; None when unknown."""
    if not division:
        return None
    return "HS" if division in HS_DIVISIONS else "MS"


def division_sort_key(division: str | None) -> tuple[int, str]:
    name = division or ""
    return (DIVISION_ORDER.index(name) if name in DIVISION_ORDER else len(DIVISION_ORDER), name)


def group_color(group: int | None) -> str:
    """Dot colour for a group number (1 = Red ... 8 = White)."""
    if not group:
        return ""
    return GROUP_COLORS[group - 1] if group <= len(GROUP_COLORS) else f"Group {group}"


def wave_joins(
    wave_format: str,
    divisions: list[str],
    custom: list[str] | None = None,
    gender: str | None = None,
) -> set:
    """Divisions (of `divisions`, already in ladder order) that share a wave with the one above."""
    below_top = set(divisions[1:])
    if wave_format == "combined":
        return below_top
    if wave_format == "custom":
        return set(custom or []) & below_top
    is_hs = any(d in ("Varsity", "JV1", "JV2", "JV3") for d in divisions)
    boys = gender == "Male"
    if wave_format == "conference":
        return (_HS_BOYS_CONFERENCE & below_top) if (is_hs and boys) else below_top
    if wave_format == "state":
        if is_hs:
            return (_HS_BOYS_CONFERENCE & below_top) if boys else below_top
        return (_MS_BOYS_STATE & below_top) if boys else below_top
    return set()


def build_grid(
    rows: list[dict],
    metric: str = "pace",
    sort: str = "best",
    division: str | None = None,
    conference: str | None = None,
    group_size: int = GROUP_SIZE,
    row_size: int = ROW_SIZE,
    wave_format: str = "conference",
    custom_joins: list[str] | None = None,
    gender: str | None = None,
    season: int | None = None,
    age_group: str | None = None,
) -> dict:
    """Build the staging grid from per-(rider, event) z-score rows.

    With `age_group`, a rider whose guessed division has moved them out of
    this age group (last season's 8th grader, JV3 now) is left out: they are
    staged on the other grid, which carries them on purpose.

    Pivots into one row per kid with a z column per race, plus Best-z and Avg-z.
    With `season`, rows from that season are the current basis and older rows
    are last season's fallback: a rider with a race this season ranks on it
    (ahead of everyone without one); a rider with only last season's races
    ranks on their prior-season *average* z behind them, in the division they
    have probably moved up to (PROMOTE_DIVISION, flagged `division_assumed`);
    a rider with neither is unrated. That is the order on the league's own
    sheets. Without `season` every row is current.

    Ranks the category (most negative = fastest first); a division and/or
    conference filter narrows to a specific race's field. A conference filter
    value matches either the specific conference (e.g. 'Eastern Blue') or its
    group (e.g. 'Eastern' = Blue + Gold), so you can model pack size both split
    and combined.

    Order is always division first (down the ladder), fastest first within a
    division, unrated riders at the back of their division with no group or
    row. `rank` is the position within the division. Each division is cut into
    groups of `group_size` (fill one, overflow into the next — no balancing,
    just like the league's sheets); a new division always opens a new group.
    The wave format says which divisions share a wave: group numbers (and
    colours) run on through every division of a wave (MS Advanced group 1,
    8th Grade groups 2-5) and begin again at 1 for the next wave. With
    `row_size`, riders also get a row within their group (row 1 is the front),
    which is what the coach writes on the coloured dot.
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
                "season": r.get("season") or 0,
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
                "plate": None,
                "_last": (-1, -1),
                "_current": False,
                "per_event": {},
                "_zs": [],
                "_prior_zs": [],
            },
        )
        z = r.get(zkey)
        z = float(z) if z is not None else None
        row_season = r.get("season") or 0
        is_current = season is None or row_season == season
        if is_current:
            rd["per_event"][eid] = z
            if z is not None:
                rd["_zs"].append(z)
        elif z is not None:
            rd["_prior_zs"].append(z)
        # Latest race wins for division/team/conference; a current-season race
        # always beats a prior one, and only a current-season plate is theirs.
        when = (1 if is_current else 0, r.get("event_order") or 0)
        if when >= rd["_last"]:
            rd["_last"] = when
            rd["division"] = r.get("division")
            rd["conference"] = r.get("conference")
            rd["conference_group"] = r.get("conference_group")
            rd["_current"] = is_current
            if is_current:
                rd["plate"] = r.get("bib") or rd["plate"]

    # Only current-season races get a column; prior seasons roll up into one.
    if season is not None:
        events = {eid: e for eid, e in events.items() if e["season"] == season}
    event_list = sorted(events.values(), key=lambda e: e["event_order"])
    divisions = sorted(
        {r["division"] for r in riders.values() if r["division"]}, key=division_sort_key
    )

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
        prior = rd.pop("_prior_zs")
        rd.pop("_last")
        current = rd.pop("_current")
        rd["best_z"] = round(min(zs), 2) if zs else None
        rd["avg_z"] = round(sum(zs) / len(zs), 2) if zs else None
        rd["n_events"] = len(zs)
        rd["prior_avg_z"] = round(sum(prior) / len(prior), 2) if prior else None
        rd["prior_n_events"] = len(prior)
        rd["division_assumed"] = False
        if not current and rd["division"]:
            promoted = PROMOTE_DIVISION.get(rd["division"])
            if promoted:
                rd["division"] = promoted
                rd["division_assumed"] = True
        if age_group and division_age_group(rd["division"]) not in (None, age_group):
            continue
        # basis: what this rider is ranked on
        if zs:
            rd["basis"] = "current"
        elif prior:
            rd["basis"] = "prior"
        else:
            rd["basis"] = None
        grid.append(rd)

    if division:
        grid = [r for r in grid if r["division"] == division]
    if conference:
        grid = [r for r in grid if conference in (r["conference"], r["conference_group"])]

    def rating(r: dict) -> float | None:
        """The z this rider is staged on: this season's aggregate, else last season's average."""
        if r["basis"] == "current":
            return r[sort_key]
        if r["basis"] == "prior":
            return r["prior_avg_z"]
        return None

    # Division first; then everyone with a race this season fastest first, then
    # last season's riders fastest first, then unrated riders.
    tier = {"current": 0, "prior": 1, None: 2}
    grid.sort(
        key=lambda r: (
            division_sort_key(r["division"]),
            tier[r["basis"]],
            rating(r) if rating(r) is not None else 0.0,
        )
    )

    staged = sorted({r["division"] for r in grid if r["division"]}, key=division_sort_key)
    if wave_format not in WAVE_FORMATS:
        wave_format = "conference"
    joins = wave_joins(wave_format, staged, custom_joins, gender)

    waves: list[dict] = []
    current = object()  # sentinel: no division yet
    wave_no = group = 0
    for r in grid:
        if r["division"] != current:
            current = r["division"]
            if current not in joins or not waves:
                wave_no += 1
                group = 0
                waves.append({"wave": wave_no, "divisions": []})
            waves[-1]["divisions"].append(current)
            in_division = in_group = in_row = row = 0
        in_division += 1
        r["rank"] = in_division
        r["wave"] = wave_no
        r["staged_z"] = rating(r)
        if r["staged_z"] is None:
            r["group"] = r["color"] = r["color_css"] = r["row"] = None
            continue
        if in_group == 0 or in_group >= group_size:
            group += 1
            in_group = 0
            in_row = row = 0  # rows count from 1 again in every group
        in_group += 1
        r["group"] = group
        r["color"] = group_color(group)
        r["color_css"] = GROUP_COLOR_CSS.get(r["color"], "#e5e7eb")
        if row_size > 0:
            if in_row == 0 or in_row >= row_size:
                row += 1
                in_row = 0
            in_row += 1
            r["row"] = row
        else:
            r["row"] = None

    return {
        "events": event_list,
        "riders": grid,
        "divisions": divisions,
        "conferences": conferences,
        "conference_groups": conference_groups,
        "metric": metric,
        "sort": sort,
        "gender": gender,
        "group_size": group_size,
        "row_size": row_size,
        "wave_format": wave_format,
        "staged_divisions": staged,
        "joins": joins,
        "waves": waves,
        "season": season,
        "rated_count": sum(1 for r in grid if r["basis"] == "current"),
        "prior_count": sum(1 for r in grid if r["basis"] == "prior"),
    }


# ── Whole-race sheet ─────────────────────────────────────────────────────

# The order the league's row sheet lists categories in: one document per race
# covering every wave, HS boys first.
SHEET_CATEGORIES = [("HS", "Male"), ("HS", "Female"), ("MS", "Female"), ("MS", "Male")]


def build_sheet(grids: list[tuple[str, str, dict]]) -> list[dict]:
    """Flatten per-category grids into the league's row-sheet rows.

    `grids` is [(age_group, gender, build_grid(...)), ...] in sheet order.
    Returns one dict per staged rider — Plate, Name, Team, Category, Wave,
    Group, Color, Row — skipping unrated riders (they have no group to write
    on a dot; the marshal adds them at the back by hand).
    """
    out = []
    for age_group, gender, grid in grids:
        for r in grid["riders"]:
            if r["group"] is None:
                continue
            out.append(
                {
                    "age_group": age_group,
                    "gender": gender,
                    "canonical_id": r["canonical_id"],
                    "plate": r["plate"] or "",
                    "name": r["name"],
                    "team": r["team"] or "",
                    "division": r["division"] or "",
                    "category": f"{r['division']} - {gender}",
                    "wave": r["wave"],
                    "group": r["group"],
                    "color": r["color"],
                    "color_css": r["color_css"],
                    "row": r["row"],
                    "rank": r["rank"],
                    "basis": r["basis"],
                    "division_assumed": r["division_assumed"],
                }
            )
    return out
