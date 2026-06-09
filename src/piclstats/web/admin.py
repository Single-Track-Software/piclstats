"""Admin router — unlinked /admin pages behind HTTP Basic auth.

Lets the operator tune forecast config and edit course stats (distance,
elevation, MS/HS loop data). Password comes from PICLSTATS_ADMIN_PASSWORD.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select, update

from piclstats.config import settings as app_settings
from piclstats.db.engine import get_session
from piclstats.db.settings_store import get_forecast_config, set_value
from piclstats.db.tables import course_loops, courses
from piclstats.web.forecast import DEFAULT_CONFIG
from piclstats.web.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(prefix="/admin", tags=["admin"])
_basic = HTTPBasic()


def _require_auth(credentials: HTTPBasicCredentials = Depends(_basic)) -> str:
    expected = app_settings.admin_password
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin password not configured",
        )
    ok_user = secrets.compare_digest(credentials.username, "admin")
    ok_pass = secrets.compare_digest(credentials.password, expected)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _require_same_origin(request: Request) -> None:
    # CSRF mitigation: reject cross-origin POSTs. Checks Origin first, falls
    # back to Referer. Host must match one of the headers so a malicious page
    # with cached Basic creds cannot write.
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    source = origin or referer
    if not source:
        raise HTTPException(status_code=403, detail="Missing Origin/Referer")
    # Extract host:port from the source URL
    try:
        from urllib.parse import urlparse
        parsed = urlparse(source)
        source_host = parsed.netloc
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid Origin/Referer")
    if source_host != host:
        raise HTTPException(status_code=403, detail="Cross-origin request blocked")


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


@router.get("", response_class=HTMLResponse)
def admin_index(request: Request, _: str = Depends(_require_auth)):
    return templates.TemplateResponse("admin/index.html", {"request": request})


@router.get("/forecast", response_class=HTMLResponse)
def forecast_form(request: Request, saved: int = 0, _: str = Depends(_require_auth)):
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
    _: str = Depends(_require_auth),
    __: None = Depends(_require_same_origin),
):
    form = await request.form()
    override: dict = {}
    for key, typ, _label in FORECAST_FIELDS:
        raw = form.get(key)
        if raw is None or raw == "":
            continue
        try:
            override[key] = typ(raw)
        except ValueError:
            raise HTTPException(400, f"Invalid value for {key}: {raw}")
    # Readiness thresholds (nested)
    try:
        override["readiness_thresholds"] = {
            "ready": int(form.get("threshold_ready", DEFAULT_CONFIG["readiness_thresholds"]["ready"])),
            "competitive": int(
                form.get("threshold_competitive", DEFAULT_CONFIG["readiness_thresholds"]["competitive"])
            ),
        }
    except ValueError:
        raise HTTPException(400, "Invalid threshold value")

    set_value("forecast_config", override)
    return RedirectResponse("/admin/forecast?saved=1", status_code=303)


@router.get("/courses", response_class=HTMLResponse)
def courses_list(request: Request, _: str = Depends(_require_auth)):
    with get_session() as s:
        rows = s.execute(
            select(courses.c.id, courses.c.name, courses.c.location, courses.c.distance_miles,
                   courses.c.elevation_ft).order_by(courses.c.name)
        ).all()
    return templates.TemplateResponse(
        "admin/courses.html", {"request": request, "courses": rows}
    )


def _load_course(course_id: int):
    with get_session() as s:
        course = s.execute(
            select(courses).where(courses.c.id == course_id)
        ).mappings().first()
        if not course:
            return None, []
        loops = s.execute(
            select(course_loops).where(course_loops.c.course_id == course_id)
            .order_by(course_loops.c.loop_type)
        ).mappings().all()
    return dict(course), [dict(l) for l in loops]


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_edit(
    request: Request, course_id: int, saved: int = 0, _: str = Depends(_require_auth)
):
    course, loops = _load_course(course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    # Ensure both MS and HS rows exist in the form, even if DB has none
    by_type = {l["loop_type"]: l for l in loops}
    for loop_type in ("MS", "HS"):
        by_type.setdefault(loop_type, {"loop_type": loop_type, "distance_miles": None, "elevation_ft": None})
    loops_display = [by_type["MS"], by_type["HS"]]
    return templates.TemplateResponse(
        "admin/course_edit.html",
        {"request": request, "course": course, "loops": loops_display, "saved": bool(saved)},
    )


def _opt_float(raw):
    if raw is None or raw == "":
        return None
    return float(raw)


@router.post("/courses/{course_id}")
async def course_save(
    request: Request,
    course_id: int,
    _: str = Depends(_require_auth),
    __: None = Depends(_require_same_origin),
):
    form = await request.form()
    try:
        distance = _opt_float(form.get("distance_miles"))
        elevation = _opt_float(form.get("elevation_ft"))
        difficulty = _opt_float(form.get("difficulty_score"))
        ms_distance = _opt_float(form.get("ms_distance_miles"))
        ms_elevation = _opt_float(form.get("ms_elevation_ft"))
        hs_distance = _opt_float(form.get("hs_distance_miles"))
        hs_elevation = _opt_float(form.get("hs_elevation_ft"))
    except ValueError:
        raise HTTPException(400, "Invalid number in form")
    location = form.get("location") or None
    notes = form.get("notes") or None

    with get_session() as s:
        s.execute(
            update(courses).where(courses.c.id == course_id).values(
                location=location,
                distance_miles=distance,
                elevation_ft=elevation,
                difficulty_score=difficulty,
                notes=notes,
            )
        )
        for loop_type, dist, elev in (
            ("MS", ms_distance, ms_elevation),
            ("HS", hs_distance, hs_elevation),
        ):
            existing = s.execute(
                select(course_loops.c.id).where(
                    (course_loops.c.course_id == course_id)
                    & (course_loops.c.loop_type == loop_type)
                )
            ).first()
            if existing:
                s.execute(
                    update(course_loops).where(course_loops.c.id == existing[0]).values(
                        distance_miles=dist, elevation_ft=elev
                    )
                )
            elif dist is not None or elev is not None:
                s.execute(
                    course_loops.insert().values(
                        course_id=course_id,
                        loop_type=loop_type,
                        distance_miles=dist,
                        elevation_ft=elev,
                    )
                )
        s.commit()

    return RedirectResponse(f"/admin/courses/{course_id}?saved=1", status_code=303)
