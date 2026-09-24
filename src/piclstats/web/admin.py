"""Admin router — unlinked /admin pages behind the admin role.

Lets the operator tune forecast config, edit course stats (MS/HS loop distance
and elevation), and manage login accounts. Auth is the shared session login
(see web/auth.py); these pages require role 'admin'.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from piclstats.db import tokens_store, users_store
from piclstats.db.engine import get_session
from piclstats.db.seed import (
    DIVISION_PROFILES,
    PROFILE_KEYS,
    RACE_TYPES,
    RIDDEN_LAPS_SQL,
    classify_event_types,
)
from piclstats.db.settings_store import get_forecast_config, set_value
from piclstats.db.tables import courses, events, scheduled_races
from piclstats.web import mail
from piclstats.web.auth import ROLE_HELP, ROLES, build_link, require_admin, require_same_origin
from piclstats.web.forecast import DEFAULT_CONFIG
from piclstats.web.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(prefix="/admin", tags=["admin"])


# Config keys we expose in the forecast form, with type + label + help text.
FORECAST_FIELDS = [
    ("recent_race_count", int, "Recent races to weight"),
    ("recency_decay", float, "Recency decay (0-1, higher = slower decay)"),
    ("fatigue_per_extra_lap", float, "Fatigue per extra lap (fraction, e.g. 0.03 = 3%)"),
    ("ms_to_hs_loop_penalty", float, "MS→HS loop penalty multiplier"),
    ("improvement_weight", float, "Seasonal improvement weight (0-1)"),
    ("min_races_for_forecast", int, "Min races needed to forecast"),
    ("climbing_impact_per_100ft_mile", float, "Pace impact per 100 ft/mi of climbing"),
    ("reference_climbing_ft_per_mile", float, "Reference climbing rate (ft/mile)"),
]


# Inclusive (lo, hi) bounds per forecast field; None = unbounded on that side.
FORECAST_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "recent_race_count": (1, 50),
    "recency_decay": (0.01, 1),
    "fatigue_per_extra_lap": (0, 1),
    "ms_to_hs_loop_penalty": (0.1, 10),
    "improvement_weight": (0, 1),
    "min_races_for_forecast": (1, 50),
    "climbing_impact_per_100ft_mile": (0, 10),
    "reference_climbing_ft_per_mile": (0, 5000),
    "threshold_ready": (0, 100),
    "threshold_competitive": (0, 100),
}


def forecast_value_error(key: str, value: float) -> str | None:
    """Why a forecast setting is unusable, or None.

    A recency decay of -1 makes two recent races weigh [-1, 1], total 0, and
    every forecast page divides by that; nan/inf produce garbage silently.
    """
    if value != value or value in (float("inf"), float("-inf")):
        return f"{key} must be a number"
    lo, hi = FORECAST_BOUNDS.get(key, (None, None))
    if lo is not None and value < lo:
        return f"{key} must be at least {lo}"
    if hi is not None and value > hi:
        return f"{key} must be at most {hi}"
    return None


def _form_str(form: FormData, key: str, default: str = "") -> str:
    """Read a form field as text.

    Starlette's FormData yields ``UploadFile`` for file parts, so a crafted
    multipart POST could otherwise slip a file object into int()/strip()/
    hash_password() and 500 the admin pages. Anything that isn't a plain string
    is treated as absent.
    """
    raw = form.get(key)
    if not isinstance(raw, str):
        return default
    return raw


@router.get("", response_class=HTMLResponse)
def admin_index(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse("admin/index.html", {"request": request})


@router.get("/forecast", response_class=HTMLResponse)
def forecast_form(request: Request, saved: int = 0, _: str = Depends(require_admin)):
    config = get_forecast_config()
    thresholds = config.get("readiness_thresholds", DEFAULT_CONFIG["readiness_thresholds"])
    return templates.TemplateResponse(
        "admin/forecast.html",
        {
            "request": request,
            "config": config,
            "fields": FORECAST_FIELDS,
            "thresholds": thresholds,
            "saved": bool(saved),
        },
    )


@router.post("/forecast")
async def forecast_save(
    request: Request,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    override: dict = {}
    for key, typ, _label in FORECAST_FIELDS:
        raw = _form_str(form, key)
        if raw == "":
            continue
        try:
            override[key] = typ(raw)
        except ValueError:
            raise HTTPException(400, f"Invalid value for {key}: {raw}")
        problem = forecast_value_error(key, override[key])
        if problem:
            raise HTTPException(400, problem)
    # Readiness thresholds (nested)
    defaults = DEFAULT_CONFIG["readiness_thresholds"]
    try:
        thresholds = {
            "ready": int(_form_str(form, "threshold_ready", str(defaults["ready"]))),
            "competitive": int(
                _form_str(form, "threshold_competitive", str(defaults["competitive"]))
            ),
        }
    except ValueError:
        raise HTTPException(400, "Invalid threshold value")
    for name, value in thresholds.items():
        problem = forecast_value_error(f"threshold_{name}", value)
        if problem:
            raise HTTPException(400, problem)
    override["readiness_thresholds"] = thresholds

    set_value("forecast_config", override)
    return RedirectResponse("/admin/forecast?saved=1", status_code=303)


def race_type_summary(rows: list[tuple[int | None, str]]) -> str:
    """One line for the courses table from (season, race_type) rows, season NULL = default.

    The headline is the default, or the type most seasons carry; the seasons
    of the other type follow: "Race", "Race · rally 2023", "Rally".
    """
    if not rows:
        return ""
    seasons_of = {
        rt: sorted(season for season, r in rows if season is not None and r == rt)
        for rt in RACE_TYPES
    }
    headline = next((rt for season, rt in rows if season is None), None) or max(
        RACE_TYPES, key=lambda rt: len(seasons_of[rt])
    )
    others = [
        f"{rt} {', '.join(map(str, ss))}" for rt, ss in seasons_of.items() if rt != headline and ss
    ]
    return headline.title() + (" · " + "; ".join(others) if others else "")


@router.get("/courses", response_class=HTMLResponse)
def courses_list(request: Request, _: str = Depends(require_admin)):
    # Only the per-loop (MS/HS) distance and elevation feed pace and forecast
    # math, so that's what the summary shows: the defaults, plus which seasons
    # carry their own rows.
    with get_session() as s:
        rows = (
            s.execute(text("SELECT id, name, location FROM courses ORDER BY name")).mappings().all()
        )
        loops = s.execute(
            text("""
            SELECT course_id, loop_type, distance_miles, elevation_ft, elevation_loss_ft
            FROM course_loops WHERE season IS NULL
        """)
        ).all()
        season_rows = s.execute(
            text("""
            SELECT course_id, season FROM course_loops WHERE season IS NOT NULL
            UNION
            SELECT course_id, season FROM division_laps WHERE season IS NOT NULL
            UNION
            SELECT course_id, season FROM course_race_types WHERE season IS NOT NULL
            ORDER BY course_id, season
        """)
        ).all()
        type_rows = s.execute(
            text("SELECT course_id, season, race_type FROM course_race_types ORDER BY season")
        ).all()
    grouped: dict[int, list[tuple[int | None, str]]] = {}
    for course_id, season, race_type in type_rows:
        grouped.setdefault(course_id, []).append((season, race_type))
    types_by_course = {cid: race_type_summary(rows_) for cid, rows_ in grouped.items()}
    by_course: dict[int, dict[str, dict]] = {}
    for course_id, loop_type, dist, elev, loss in loops:
        by_course.setdefault(course_id, {})[loop_type] = {
            "distance_miles": dist,
            "elevation_ft": elev,
            "elevation_loss_ft": loss,
        }
    seasons_by_course: dict[int, list[int]] = {}
    for course_id, season in season_rows:
        seasons_by_course.setdefault(course_id, []).append(season)
    course_rows = [
        {
            **row,
            "loops": by_course.get(row["id"], {}),
            "seasons": seasons_by_course.get(row["id"], []),
            "race_types": types_by_course.get(row["id"], ""),
        }
        for row in rows
    ]
    return templates.TemplateResponse(
        "admin/courses.html", {"request": request, "courses": course_rows}
    )


# ── Per-season course profiles ─────────────────────────────────────────
#
# A course has a default profile (season NULL) and optional per-season rows.
# Each profile = MS/HS loop distance + elevation and a lap count per
# (division, gender). Queries resolve the event's season row first and fall
# back to the default, so a season block only needs the values that differ.


@dataclass
class ProfileForm:
    """Parsed per-season profile form. None = field left blank."""

    loops: dict[str, tuple[float | None, float | None, float | None]]  # -> (miles, gain, loss)
    laps: dict[int, int | None]  # index into PROFILE_KEYS -> lap count
    race_type: str | None = None  # 'race' | 'rally' | None (blank = default / by name)


def parse_profile_form(form: Mapping[str, str]) -> ProfileForm:
    """Parse the loop, lap and race-type fields of a season block. Raises ValueError on bad input."""

    def opt_float(key: str) -> float | None:
        raw = form.get(key, "").strip()
        return float(raw) if raw else None

    race_type: str | None = form.get("race_type", "").strip().lower() or None
    if race_type is not None and race_type not in RACE_TYPES:
        raise ValueError(f"Race type must be one of {', '.join(RACE_TYPES)}, got {race_type!r}")

    loops: dict[str, tuple[float | None, float | None, float | None]] = {}
    for loop_type in ("MS", "HS"):
        prefix = loop_type.lower()
        loops[loop_type] = (
            opt_float(f"{prefix}_distance_miles"),
            opt_float(f"{prefix}_elevation_ft"),
            opt_float(f"{prefix}_elevation_loss_ft"),
        )
    laps: dict[int, int | None] = {}
    for i in range(len(PROFILE_KEYS)):
        raw = form.get(f"lap_{i}", "").strip()
        if raw == "":
            laps[i] = None
            continue
        count = int(raw)
        if not 1 <= count <= 6:
            raise ValueError(f"Lap count must be 1-6, got {raw}")
        laps[i] = count
    return ProfileForm(loops=loops, laps=laps, race_type=race_type)


def _season_key(season: int | None) -> str:
    return "default" if season is None else str(season)


def _parse_season_key(key: str) -> int | None:
    if key == "default":
        return None
    try:
        season = int(key)
    except ValueError:
        raise HTTPException(404, "Unknown season")
    if not 2000 <= season <= 2100:
        raise HTTPException(404, "Unknown season")
    return season


def _course_seasons(s: Session, course_id: int) -> list[int]:
    """Seasons the course has raced in or has explicit profile rows for, newest first."""
    rows = s.execute(
        text("""
        SELECT season FROM events WHERE course_id = :cid AND season > 0
        UNION SELECT season FROM course_loops WHERE course_id = :cid AND season IS NOT NULL
        UNION SELECT season FROM division_laps WHERE course_id = :cid AND season IS NOT NULL
        UNION SELECT season FROM course_race_types WHERE course_id = :cid AND season IS NOT NULL
        ORDER BY season DESC
    """),
        {"cid": course_id},
    ).all()
    return [r[0] for r in rows]


def _recorded_laps(
    s: Session, course_id: int
) -> dict[tuple[int, str, str | None], tuple[int, int]]:
    """(season, division, gender) -> (most common laps recorded by OK finishers, finishers)."""
    rows = s.execute(
        text(f"""
        SELECT e.season, r.division, r.gender,
               mode() WITHIN GROUP (ORDER BY {RIDDEN_LAPS_SQL}) AS laps,
               count(*) AS finishers
        FROM results r
        JOIN events e ON e.id = r.event_id
        WHERE e.course_id = :cid AND e.event_type = 'points'
          AND r.status = 'OK' AND r.total_time IS NOT NULL AND {RIDDEN_LAPS_SQL} > 0
        GROUP BY e.season, r.division, r.gender
    """),
        {"cid": course_id},
    ).all()
    return {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows}


def _profile_block(
    s: Session,
    course_id: int,
    season: int | None,
    recorded: dict[tuple[int, str, str | None], tuple[int, int]],
) -> dict:
    """Everything the template needs to render one season block (or the defaults)."""
    loop_rows = s.execute(
        text("""
        SELECT loop_type, distance_miles, elevation_ft, season, elevation_loss_ft
        FROM course_loops
        WHERE course_id = :cid AND (season IS NULL OR season = :season)
    """),
        {"cid": course_id, "season": season},
    ).all()
    lap_rows = s.execute(
        text("""
        SELECT division, gender, lap_count, season
        FROM division_laps
        WHERE course_id = :cid AND (season IS NULL OR season = :season)
    """),
        {"cid": course_id, "season": season},
    ).all()

    type_rows = s.execute(
        text("""
        SELECT season, race_type FROM course_race_types
        WHERE course_id = :cid AND (season IS NULL OR season = :season)
    """),
        {"cid": course_id, "season": season},
    ).all()
    own_type = next((r[1] for r in type_rows if r[0] == season), None)
    default_type = next((r[1] for r in type_rows if r[0] is None), None)
    # What classify_event_types will use: the season row, else the default,
    # else the '%rally%' name pattern. Shown beside the select.
    if season is not None and own_type is None:
        named_rally = s.execute(
            text("""
            SELECT bool_or(event_name ILIKE '%rally%') FROM events
            WHERE course_id = :cid AND season = :season
        """),
            {"cid": course_id, "season": season},
        ).scalar()
    else:
        named_rally = None
    if own_type:
        in_effect, in_effect_from = own_type, "this season"
    elif default_type:
        in_effect, in_effect_from = default_type, "course default"
    elif named_rally is not None:
        in_effect, in_effect_from = ("rally" if named_rally else "race"), "event name"
    else:
        in_effect, in_effect_from = None, "event name"

    loops: dict[str, dict] = {}
    for loop_type in ("MS", "HS"):
        own = next((r for r in loop_rows if r[0] == loop_type and r[3] == season), None)
        default = next((r for r in loop_rows if r[0] == loop_type and r[3] is None), None)
        loops[loop_type] = {
            "distance_miles": own[1] if own else None,
            "elevation_ft": own[2] if own else None,
            "elevation_loss_ft": own[4] if own else None,
            "fallback_distance": default[1] if default else None,
            "fallback_elevation": default[2] if default else None,
            "fallback_loss": default[4] if default else None,
        }

    laps = []
    for i, (division, gender) in enumerate(PROFILE_KEYS):
        own = next(
            (r for r in lap_rows if (r[0], r[1]) == (division, gender) and r[3] == season), None
        )
        default = next(
            (r for r in lap_rows if (r[0], r[1]) == (division, gender) and r[3] is None), None
        )
        rec = recorded.get((season, division, gender)) if season is not None else None
        laps.append(
            {
                "key": f"lap_{i}",
                "division": division,
                "gender": gender or "",
                "lap_count": own[2] if own else None,
                "fallback": default[2] if default else None,
                "recorded": rec[0] if rec else None,
                "finishers": rec[1] if rec else None,
                "mismatch": bool(
                    rec and (own[2] if own else default[2] if default else None) != rec[0]
                ),
            }
        )
    return {
        "season": season,
        "key": _season_key(season),
        "race_type": own_type,
        "race_type_in_effect": in_effect,
        "race_type_from": in_effect_from,
        "loops": loops,
        "laps": laps,
    }


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_edit(
    request: Request,
    course_id: int,
    saved: str = "",
    add: int | None = None,
    _: str = Depends(require_admin),
):
    with get_session() as s:
        course = (
            s.execute(text("SELECT * FROM courses WHERE id = :id"), {"id": course_id})
            .mappings()
            .first()
        )
        if not course:
            raise HTTPException(404, "Course not found")
        seasons = _course_seasons(s, course_id)
        if add is not None and 2000 <= add <= 2100 and add not in seasons:
            seasons = sorted(seasons + [add], reverse=True)
        recorded = _recorded_laps(s, course_id)
        blocks = [_profile_block(s, course_id, season, recorded) for season in seasons]
        defaults = _profile_block(s, course_id, None, recorded)
    return templates.TemplateResponse(
        "admin/course_edit.html",
        {
            "request": request,
            "course": dict(course),
            "blocks": blocks,
            "defaults": defaults,
            "saved": saved,
        },
    )


def _opt_float(form: FormData, key: str) -> float | None:
    raw = _form_str(form, key)
    if raw == "":
        return None
    return float(raw)


@router.post("/courses/{course_id}")
async def course_save(
    request: Request,
    course_id: int,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    try:
        difficulty = _opt_float(form, "difficulty_score")
    except ValueError:
        raise HTTPException(400, "Invalid number in form")
    location = _form_str(form, "location") or None
    notes = _form_str(form, "notes") or None

    with get_session() as s:
        s.execute(
            update(courses)
            .where(courses.c.id == course_id)
            .values(location=location, difficulty_score=difficulty, notes=notes)
        )
        s.commit()

    return RedirectResponse(f"/admin/courses/{course_id}?saved=course", status_code=303)


@router.post("/courses/{course_id}/profile/{season_key}")
async def profile_save(
    request: Request,
    course_id: int,
    season_key: str,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    """Save one season block (or the defaults).

    Blank fields in a season block remove that season's row so the default
    applies again. Blank fields in the defaults block are left unchanged —
    every query needs a default to fall back to.
    """
    season = _parse_season_key(season_key)
    form = await request.form()
    fields = {k: v for k, v in form.items() if isinstance(v, str)}
    try:
        parsed = parse_profile_form(fields)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid number in form: {exc}")

    with get_session() as s:
        exists = s.execute(text("SELECT 1 FROM courses WHERE id = :id"), {"id": course_id}).first()
        if not exists:
            raise HTTPException(404, "Course not found")

        if parsed.race_type is None:
            s.execute(
                text("""
                DELETE FROM course_race_types
                WHERE course_id = :cid AND season IS NOT DISTINCT FROM :season
            """),
                {"cid": course_id, "season": season},
            )
        else:
            s.execute(
                text("""
                INSERT INTO course_race_types (course_id, season, race_type)
                VALUES (:cid, :season, :rt)
                ON CONFLICT (course_id, season) DO UPDATE SET race_type = :rt
            """),
                {"cid": course_id, "season": season, "rt": parsed.race_type},
            )

        for loop_type, (dist, elev, loss) in parsed.loops.items():
            params = {"cid": course_id, "lt": loop_type, "season": season}
            if dist is None and elev is None and loss is None:
                if season is not None:
                    s.execute(
                        text("""
                        DELETE FROM course_loops
                        WHERE course_id = :cid AND loop_type = :lt AND season = :season
                    """),
                        params,
                    )
                continue
            s.execute(
                text("""
                INSERT INTO course_loops (course_id, loop_type, distance_miles, elevation_ft,
                    elevation_loss_ft, season)
                VALUES (:cid, :lt, :dist, :elev, :loss, :season)
                ON CONFLICT (course_id, loop_type, season)
                DO UPDATE SET distance_miles = :dist, elevation_ft = :elev,
                    elevation_loss_ft = :loss
            """),
                {**params, "dist": dist, "elev": elev, "loss": loss},
            )

        for i, (division, gender) in enumerate(PROFILE_KEYS):
            laps = parsed.laps.get(i)
            params = {"cid": course_id, "div": division, "gender": gender, "season": season}
            if laps is None:
                if season is not None:
                    s.execute(
                        text("""
                        DELETE FROM division_laps
                        WHERE course_id = :cid AND division = :div
                          AND gender IS NOT DISTINCT FROM :gender AND season = :season
                    """),
                        params,
                    )
                continue
            # Duration/cutoff/loop type aren't edited here; copy them from the
            # course default (seeded for every division) or the league profile.
            profile = next(
                (p for p in DIVISION_PROFILES if p[0] == division and p[1] == gender), None
            )
            s.execute(
                text("""
                INSERT INTO division_laps (course_id, division, gender, lap_count,
                    max_duration_mins, cutoff_mins, loop_type, season)
                SELECT :cid, :div, :gender, :laps,
                       COALESCE(d.max_duration_mins, :max_dur),
                       COALESCE(d.cutoff_mins, :cutoff),
                       COALESCE(d.loop_type, :lt),
                       :season
                FROM (SELECT 1) one
                LEFT JOIN division_laps d ON d.course_id = :cid AND d.division = :div
                    AND d.gender IS NOT DISTINCT FROM :gender AND d.season IS NULL
                ON CONFLICT (course_id, division, gender, season)
                DO UPDATE SET lap_count = :laps
            """),
                {
                    **params,
                    "laps": laps,
                    "max_dur": profile[3] if profile else None,
                    "cutoff": profile[4] if profile else None,
                    "lt": profile[5] if profile else None,
                },
            )
        classify_event_types(s)
        s.commit()

    return RedirectResponse(
        f"/admin/courses/{course_id}?saved={_season_key(season)}#season-{_season_key(season)}",
        status_code=303,
    )


# --- user management --------------------------------------------------------

VALID_ROLES = ROLES


def _users_page(
    request: Request,
    saved: str = "",
    error: str = "",
    invite_link: str = "",
    invite_email: str = "",
    emailed: bool = True,
    link_kind: str = "invite",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "users": users_store.list_users(),
            "invites": tokens_store.list_pending_invites(),
            "saved": saved,
            "error": error,
            "roles": VALID_ROLES,
            "role_help": ROLE_HELP,
            # Shown once, immediately after minting — the raw token is not
            # recoverable afterwards, only its hash is stored.
            "invite_link": invite_link,
            "invite_email": invite_email,
            "emailed": emailed,
            "link_kind": link_kind,  # 'invite' | 'reset'
            "reset_hours": max(1, int(tokens_store.RESET_TTL.total_seconds() // 3600)),
            "email_configured": mail.is_configured(),
        },
        status_code=status_code,
    )


# ── Season schedule and race dates ─────────────────────────────────────
#
# The calendar of races still to come (scheduled_races) feeds the forecast's
# future-races table: the course gives the lap counts, the field says who
# turns up. Loaded events get their race date here too.

STATE_FIELD = "State"


def _squash(value: str) -> str:
    """Collapse whitespace; conference names arrive as 'Eastern  Blue' some seasons."""
    return " ".join(value.split())


def parse_schedule_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError(f"'{raw.strip()}' is not a date (use YYYY-MM-DD)") from None


def parse_schedule_field(raw: str, conferences: list[str]) -> str | None:
    """'State' (or blank) -> None; otherwise one of the season's conferences."""
    wanted = _squash(raw)
    if wanted == "" or wanted.lower() == STATE_FIELD.lower():
        return None
    for conf in conferences:
        if conf.lower() == wanted.lower():
            return conf
    raise ValueError(f"'{wanted}' is not State or one of: {', '.join(conferences)}")


def parse_schedule_lines(
    raw: str, course_ids: Mapping[str, int], conferences: list[str]
) -> list[dict[str, Any]]:
    """Parse the paste-in schedule: one `date | name | course | field` per line.

    All or nothing — every bad line is reported, by number, in one ValueError.
    `course_ids` maps lower-cased course name to id.
    """
    races: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        try:
            if len(parts) != 4:
                raise ValueError("expected 4 parts: date | name | course | field")
            when, name, course, field = parts
            if not name:
                raise ValueError("race name is blank")
            course_id = course_ids.get(_squash(course).lower())
            if course_id is None:
                raise ValueError(f"unknown course '{course}'")
            races.append(
                {
                    "event_date": parse_schedule_date(when),
                    "name": _squash(name),
                    "course_id": course_id,
                    "conference": parse_schedule_field(field, conferences),
                }
            )
        except ValueError as exc:
            errors.append(f"Line {number}: {exc}")
    if errors:
        raise ValueError("; ".join(errors))
    return races


def _season_conferences(s: Session, season: int) -> list[str]:
    """That season's conferences, else the latest season that has any."""
    rows = s.execute(
        text("""
        SELECT DISTINCT conference FROM team_conferences
        WHERE season = (
            SELECT max(season) FROM team_conferences
            WHERE season <= :season OR NOT EXISTS (
                SELECT 1 FROM team_conferences WHERE season <= :season
            )
        )
    """),
        {"season": season},
    ).all()
    return sorted({_squash(r[0]) for r in rows})


def _schedule_redirect(season: int, **params: str) -> RedirectResponse:
    query = "".join(f"&{k}={quote(v)}" for k, v in params.items())
    return RedirectResponse(f"/admin/schedule?season={season}{query}", status_code=303)


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(
    request: Request,
    season: int | None = None,
    saved: str = "",
    error: str = "",
    _: str = Depends(require_admin),
):
    this_year = date.today().year
    with get_session() as s:
        seasons = {
            r[0]
            for r in s.execute(
                text("SELECT season FROM events UNION SELECT season FROM scheduled_races")
            ).all()
        } | {this_year, this_year + 1}
        if season is None or not 2000 <= season <= 2100:
            season = this_year
        races = (
            s.execute(
                text("""
            SELECT sr.id, sr.event_date, sr.name, sr.course_id, sr.conference, c.name AS course
            FROM scheduled_races sr JOIN courses c ON c.id = sr.course_id
            WHERE sr.season = :season ORDER BY sr.event_date, sr.name
        """),
                {"season": season},
            )
            .mappings()
            .all()
        )
        loaded = (
            s.execute(
                text("""
            SELECT e.id, e.event_name, e.event_order, e.event_date, e.event_type, c.name AS course
            FROM events e LEFT JOIN courses c ON c.id = e.course_id
            WHERE e.season = :season ORDER BY e.event_order, e.id
        """),
                {"season": season},
            )
            .mappings()
            .all()
        )
        course_rows = s.execute(text("SELECT id, name FROM courses ORDER BY name")).mappings().all()
        conferences = _season_conferences(s, season)
    return templates.TemplateResponse(
        "admin/schedule.html",
        {
            "request": request,
            "season": season,
            "seasons": sorted(seasons, reverse=True),
            "races": [dict(r) for r in races],
            "loaded": [dict(r) for r in loaded],
            "courses": [dict(r) for r in course_rows],
            "conferences": conferences,
            "state_field": STATE_FIELD,
            "today": date.today(),
            "saved": saved,
            "error": error,
        },
    )


def _race_from_form(s: Session, form: FormData, season: int) -> dict[str, Any]:
    name = _squash(_form_str(form, "name"))
    if not name:
        raise ValueError("race name is blank")
    try:
        course_id = int(_form_str(form, "course_id"))
    except ValueError:
        raise ValueError("pick a course") from None
    if not s.execute(text("SELECT 1 FROM courses WHERE id = :id"), {"id": course_id}).first():
        raise ValueError("pick a course")
    return {
        "event_date": parse_schedule_date(_form_str(form, "event_date")),
        "name": name,
        "course_id": course_id,
        "conference": parse_schedule_field(
            _form_str(form, "conference"), _season_conferences(s, season)
        ),
    }


@router.post("/schedule/{season}/add")
async def schedule_add(
    request: Request,
    season: int,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    with get_session() as s:
        try:
            s.execute(
                insert(scheduled_races).values(season=season, **_race_from_form(s, form, season))
            )
            s.commit()
        except ValueError as exc:
            return _schedule_redirect(season, error=str(exc))
        except IntegrityError:
            return _schedule_redirect(season, error="That race is already on the schedule")
    return _schedule_redirect(season, saved="race")


@router.post("/schedule/{season}/import")
async def schedule_import(
    request: Request,
    season: int,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    with get_session() as s:
        course_ids = {
            _squash(name).lower(): cid
            for cid, name in s.execute(text("SELECT id, name FROM courses")).all()
        }
        try:
            races = parse_schedule_lines(
                _form_str(form, "lines"), course_ids, _season_conferences(s, season)
            )
            if not races:
                raise ValueError("nothing to import")
            s.execute(insert(scheduled_races), [{"season": season, **r} for r in races])
            s.commit()
        except ValueError as exc:
            return _schedule_redirect(season, error=str(exc))
        except IntegrityError:
            return _schedule_redirect(
                season, error="One of those races is already on the schedule; nothing imported"
            )
    return _schedule_redirect(season, saved=f"{len(races)} races")


@router.post("/schedule/race/{race_id}")
async def schedule_update(
    request: Request,
    race_id: int,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    with get_session() as s:
        season = s.execute(
            text("SELECT season FROM scheduled_races WHERE id = :id"), {"id": race_id}
        ).scalar()
        if season is None:
            raise HTTPException(404, "Scheduled race not found")
        if _form_str(form, "action") == "delete":
            s.execute(delete(scheduled_races).where(scheduled_races.c.id == race_id))
            s.commit()
            return _schedule_redirect(season, saved="deleted")
        try:
            s.execute(
                update(scheduled_races)
                .where(scheduled_races.c.id == race_id)
                .values(**_race_from_form(s, form, season))
            )
            s.commit()
        except ValueError as exc:
            return _schedule_redirect(season, error=str(exc))
        except IntegrityError:
            return _schedule_redirect(season, error="That race is already on the schedule")
    return _schedule_redirect(season, saved="race")


@router.post("/schedule/{season}/event-dates")
async def schedule_event_dates(
    request: Request,
    season: int,
    _: str = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    """Save the race date of every loaded event in the season (blank clears it)."""
    form = await request.form()
    with get_session() as s:
        ids = [
            r[0] for r in s.execute(text("SELECT id FROM events WHERE season = :s"), {"s": season})
        ]
        try:
            for event_id in ids:
                raw = _form_str(form, f"event_date_{event_id}").strip()
                s.execute(
                    update(events)
                    .where(events.c.id == event_id)
                    .values(event_date=parse_schedule_date(raw) if raw else None)
                )
            s.commit()
        except ValueError as exc:
            return _schedule_redirect(season, error=str(exc))
    return _schedule_redirect(season, saved="dates")


@router.get("/dq", response_class=HTMLResponse)
def dq_page(
    request: Request,
    q: str = "",
    check: str = "",
    error: str = "",
    _: dict = Depends(require_admin),
):
    """Data-quality control tower: pipeline flow, scorecard, findings, lineage."""
    from piclstats.quality import dqpage

    with get_session() as s:
        data = dqpage.page(s, q=q, check=check or None)
    return templates.TemplateResponse("admin/dq.html", {"request": request, "error": error, **data})


@router.post("/dq/publish")
async def dq_publish(
    request: Request,
    _: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    """Publish or hide one event by hand (overrides the gate)."""
    form = await request.form()
    event_id = int(_form_str(form, "event_id") or 0)
    publish = _form_str(form, "publish") == "1"
    with get_session() as s:
        s.execute(
            text("UPDATE events SET is_published = :p WHERE id = :id"),
            {"p": publish, "id": event_id},
        )
        # Keep the gate's reason as history, but say what happened to it, so the
        # page never reads as if the race were still held.
        s.execute(
            text("""
            UPDATE discovered_events SET status = :st, updated_at = now(),
                note = CASE WHEN note IS NULL OR note = '' THEN :what
                            WHEN note LIKE '%by hand%' THEN note
                            ELSE :what || ' (gate said: ' || note || ')' END
            WHERE raceresult_id = (SELECT raceresult_id FROM events WHERE id = :id)
            """),
            {
                "st": "published" if publish else "blocked",
                "id": event_id,
                "what": ("published" if publish else "hidden") + " by hand",
            },
        )
        s.commit()
    return RedirectResponse("/admin/dq", status_code=303)


@router.post("/dq/golden")
async def dq_golden_add(
    request: Request,
    _: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    """Pin a fact the scorecard must keep true (ADR 002 golden fixtures)."""
    from piclstats.quality.scorecard import add_golden

    form = await request.form()
    kind = _form_str(form, "kind")
    note = _form_str(form, "note") or None
    subject: dict[str, Any]
    expected: dict[str, Any]
    try:
        if kind == "rider_canonical":
            subject = {"rider_id": int(_form_str(form, "rider_id"))}
            expected = {"canonical_id": int(_form_str(form, "canonical_id"))}
        elif kind == "event_type":
            subject = {"raceresult_id": int(_form_str(form, "raceresult_id"))}
            expected = {"type": _form_str(form, "value").strip()}
        elif kind == "event_course":
            subject = {"raceresult_id": int(_form_str(form, "raceresult_id"))}
            expected = {"course": _form_str(form, "value").strip()}
        else:
            raise ValueError("unknown kind")
        if not all(expected.values()):
            raise ValueError("missing expected value")
    except ValueError:
        return RedirectResponse(
            "/admin/dq?error=Could+not+read+that+fixture#golden", status_code=303
        )
    with get_session() as s:
        add_golden(s, kind, subject, expected, note)
    return RedirectResponse("/admin/dq#golden", status_code=303)


@router.post("/dq/golden/{golden_id}/delete")
async def dq_golden_delete(
    golden_id: int,
    _: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    with get_session() as s:
        s.execute(text("DELETE FROM picl_golden WHERE id = :id"), {"id": golden_id})
        s.commit()
    return RedirectResponse("/admin/dq#golden", status_code=303)


@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request, days: int = 30, _: dict = Depends(require_admin)):
    """Who uses the site and what they look at (first-party log, no cookies)."""
    from piclstats.web import usagepage

    days = days if days in (7, 30, 90) else 30
    with get_session() as s:
        data = usagepage.page(s, days=days)
    return templates.TemplateResponse("admin/usage.html", {"request": request, **data})


@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request, saved: str = "", error: str = "", _: dict = Depends(require_admin)
):
    return _users_page(request, saved=saved, error=error)


@router.post("/users/invite", response_class=HTMLResponse)
async def users_invite(
    request: Request,
    admin: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    """Issue a one-time invite link and email it.

    Renders the page directly rather than redirecting: the link carries a live
    token, and a redirect would put it in the URL bar, browser history, and
    every proxy log along the way.
    """
    form = await request.form()
    email = _form_str(form, "email").strip()
    role = _form_str(form, "role") or "coach"

    if not email:
        return _users_page(request, error="Email required", status_code=400)
    if role not in VALID_ROLES:
        return _users_page(request, error="Invalid role", status_code=400)
    if users_store.get_user_by_email(email):
        return _users_page(request, error=f"{email} already has an account.", status_code=409)

    # Re-inviting the same address retires the earlier link rather than leaving
    # two live invites for one person.
    tokens_store.invalidate_outstanding(email, tokens_store.INVITE)
    token = tokens_store.create(
        purpose=tokens_store.INVITE, email=email, role=role, created_by=admin["id"]
    )
    link = build_link(request, f"/invite/{token}")
    days = max(1, tokens_store.INVITE_TTL.days)
    sent = mail.send_invite(email, link, admin.get("name"), days)
    # Unconfigured mail "succeeds" by logging, so only claim an email went out
    # when one really could have; the admin passes the link on otherwise.
    emailed = sent and mail.is_configured()

    saved = f"Invite sent to {email}." if emailed else ""
    error = "" if sent else f"Invite created, but the email to {email} failed to send."
    return _users_page(
        request, saved=saved, error=error, invite_link=link, invite_email=email, emailed=emailed
    )


@router.post("/users/invite/{token_id}/revoke")
def users_invite_revoke(
    token_id: int,
    _: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    tokens_store.revoke(token_id)
    return RedirectResponse("/admin/users?saved=Invite+revoked", status_code=303)


def user_action_block(
    action: str, role: str | None, target: Mapping, admin: Mapping, active_admins: int
) -> str | None:
    """Why an admin action on a user must be refused, or None if it is fine.

    Two lock-out paths are closed: an admin changing their own access (the
    form posts set_role, not "demote", so the role check has to look at the
    requested role), and removing the last active admin, after which nobody
    could invite or repair anything without a database edit.
    """
    removes_access = action == "deactivate" or (action == "set_role" and role != "admin")
    if not removes_access:
        return None
    if target["id"] == admin["id"]:
        return "You cannot change your own access"
    if target["role"] == "admin" and target["is_active"] and active_admins <= 1:
        return "Cannot remove the last active admin"
    return None


@router.post("/users/{user_id}")
async def users_update(
    request: Request,
    user_id: int,
    admin: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    action = _form_str(form, "action")

    target = users_store.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")

    role = (_form_str(form, "role") or "coach") if action == "set_role" else None
    if role is not None and role not in VALID_ROLES:
        return RedirectResponse("/admin/users?error=Invalid+role", status_code=303)

    active_admins = sum(
        1 for u in users_store.list_users() if u["role"] == "admin" and u["is_active"]
    )
    blocked = user_action_block(action, role, target, admin, active_admins)
    if blocked:
        return RedirectResponse("/admin/users?error=" + quote(blocked), status_code=303)

    if action == "set_role":
        assert role is not None
        users_store.set_role(user_id, role)
        return RedirectResponse("/admin/users?saved=Role+updated", status_code=303)
    if action == "activate":
        users_store.set_active(user_id, True)
        return RedirectResponse("/admin/users?saved=User+activated", status_code=303)
    if action == "deactivate":
        users_store.set_active(user_id, False)
        return RedirectResponse("/admin/users?saved=User+deactivated", status_code=303)
    if action == "send_reset":
        # Admins send a reset link rather than setting a password themselves, so
        # no password ever passes through an admin or a chat window.
        tokens_store.invalidate_outstanding(target["email"], tokens_store.RESET)
        token = tokens_store.create(
            purpose=tokens_store.RESET, email=target["email"], user_id=user_id
        )
        hours = max(1, int(tokens_store.RESET_TTL.total_seconds() // 3600))
        link = build_link(request, f"/reset/{token}")
        sent = mail.send_password_reset(target["email"], link, hours)
        emailed = sent and mail.is_configured()
        # Like an invite, render the page with the live link rather than
        # redirecting: when email is off (or fails) the admin hands the link
        # over themselves, and a redirect would leak the token into the URL.
        return _users_page(
            request,
            saved=f"Reset link sent to {target['email']}." if emailed else "",
            error=""
            if sent
            else f"Reset link created, but the email to {target['email']} failed to send.",
            invite_link=link,
            invite_email=target["email"],
            emailed=emailed,
            link_kind="reset",
        )

    return RedirectResponse("/admin/users?error=Unknown+action", status_code=303)
