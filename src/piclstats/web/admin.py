"""Admin router — unlinked /admin pages behind the admin role.

Lets the operator tune forecast config, edit course stats (distance, elevation,
MS/HS loop data), and manage login accounts. Auth is the shared session login
(see web/auth.py); these pages require role 'admin'.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from starlette.datastructures import FormData

from piclstats.db import users_store
from piclstats.db.engine import get_session
from piclstats.db.settings_store import get_forecast_config, set_value
from piclstats.db.tables import course_loops, courses
from piclstats.web.auth import hash_password, require_admin, require_same_origin
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
    # Readiness thresholds (nested)
    defaults = DEFAULT_CONFIG["readiness_thresholds"]
    try:
        override["readiness_thresholds"] = {
            "ready": int(_form_str(form, "threshold_ready", str(defaults["ready"]))),
            "competitive": int(
                _form_str(form, "threshold_competitive", str(defaults["competitive"]))
            ),
        }
    except ValueError:
        raise HTTPException(400, "Invalid threshold value")

    set_value("forecast_config", override)
    return RedirectResponse("/admin/forecast?saved=1", status_code=303)


@router.get("/courses", response_class=HTMLResponse)
def courses_list(request: Request, _: str = Depends(require_admin)):
    with get_session() as s:
        rows = s.execute(
            select(
                courses.c.id,
                courses.c.name,
                courses.c.location,
                courses.c.distance_miles,
                courses.c.elevation_ft,
            ).order_by(courses.c.name)
        ).all()
    return templates.TemplateResponse("admin/courses.html", {"request": request, "courses": rows})


def _load_course(course_id: int):
    with get_session() as s:
        course = s.execute(select(courses).where(courses.c.id == course_id)).mappings().first()
        if not course:
            return None, []
        loops = (
            s.execute(
                select(course_loops)
                .where(course_loops.c.course_id == course_id)
                .order_by(course_loops.c.loop_type)
            )
            .mappings()
            .all()
        )
    return dict(course), [dict(loop) for loop in loops]


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_edit(request: Request, course_id: int, saved: int = 0, _: str = Depends(require_admin)):
    course, loops = _load_course(course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    # Ensure both MS and HS rows exist in the form, even if DB has none
    by_type = {loop["loop_type"]: loop for loop in loops}
    for loop_type in ("MS", "HS"):
        by_type.setdefault(
            loop_type, {"loop_type": loop_type, "distance_miles": None, "elevation_ft": None}
        )
    loops_display = [by_type["MS"], by_type["HS"]]
    return templates.TemplateResponse(
        "admin/course_edit.html",
        {"request": request, "course": course, "loops": loops_display, "saved": bool(saved)},
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
        distance = _opt_float(form, "distance_miles")
        elevation = _opt_float(form, "elevation_ft")
        difficulty = _opt_float(form, "difficulty_score")
        ms_distance = _opt_float(form, "ms_distance_miles")
        ms_elevation = _opt_float(form, "ms_elevation_ft")
        hs_distance = _opt_float(form, "hs_distance_miles")
        hs_elevation = _opt_float(form, "hs_elevation_ft")
    except ValueError:
        raise HTTPException(400, "Invalid number in form")
    location = _form_str(form, "location") or None
    notes = _form_str(form, "notes") or None

    with get_session() as s:
        s.execute(
            update(courses)
            .where(courses.c.id == course_id)
            .values(
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
                    update(course_loops)
                    .where(course_loops.c.id == existing[0])
                    .values(distance_miles=dist, elevation_ft=elev)
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


# --- user management --------------------------------------------------------

VALID_ROLES = ("member", "admin")


@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request, saved: str = "", error: str = "", _: dict = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "users": users_store.list_users(),
            "saved": saved,
            "error": error,
            "roles": VALID_ROLES,
        },
    )


@router.post("/users")
async def users_create(
    request: Request,
    admin: dict = Depends(require_admin),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    email = _form_str(form, "email").strip()
    name = _form_str(form, "name").strip() or None
    role = _form_str(form, "role") or "member"
    password = _form_str(form, "password")

    if not email or not password:
        return RedirectResponse("/admin/users?error=Email+and+password+required", status_code=303)
    if role not in VALID_ROLES:
        return RedirectResponse("/admin/users?error=Invalid+role", status_code=303)
    if users_store.get_user_by_email(email):
        return RedirectResponse("/admin/users?error=Email+already+exists", status_code=303)

    users_store.create_user(email, name, hash_password(password), role)
    return RedirectResponse("/admin/users?saved=User+created", status_code=303)


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

    # Don't let an admin lock themselves out of the last admin path.
    if action in ("deactivate", "demote") and target["id"] == admin["id"]:
        return RedirectResponse(
            "/admin/users?error=You+cannot+change+your+own+access", status_code=303
        )

    if action == "set_role":
        role = _form_str(form, "role") or "member"
        if role not in VALID_ROLES:
            return RedirectResponse("/admin/users?error=Invalid+role", status_code=303)
        users_store.set_role(user_id, role)
        return RedirectResponse("/admin/users?saved=Role+updated", status_code=303)
    if action == "activate":
        users_store.set_active(user_id, True)
        return RedirectResponse("/admin/users?saved=User+activated", status_code=303)
    if action == "deactivate":
        users_store.set_active(user_id, False)
        return RedirectResponse("/admin/users?saved=User+deactivated", status_code=303)
    if action == "reset_password":
        password = _form_str(form, "password")
        if not password:
            return RedirectResponse("/admin/users?error=Password+required", status_code=303)
        users_store.set_password(user_id, hash_password(password))
        return RedirectResponse("/admin/users?saved=Password+reset", status_code=303)

    return RedirectResponse("/admin/users?error=Unknown+action", status_code=303)
