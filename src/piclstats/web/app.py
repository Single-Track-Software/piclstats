"""FastAPI web dashboard for PICL Stats."""

from __future__ import annotations

import csv
import io
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from piclstats.config import settings
from piclstats.db.engine import get_session
from piclstats.web.templating import Jinja2Templates
from piclstats.web import queries
from piclstats.web.auth import (
    LoginRequired,
    load_user,
    require_member,
    require_member_api,
)

TEMPLATE_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Names resolve at call time, so the handlers can be defined further down.
    _check_session_secret()
    _bootstrap_admin()
    yield


app = FastAPI(title="PICL Stats Dashboard", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# Middleware runs outermost-last, so add the user-context middleware first and
# SessionMiddleware second — SessionMiddleware then wraps it and request.session
# is populated before _load_user_state runs.
@app.middleware("http")
async def _load_user_state(request: Request, call_next):
    # Expose the current user to every template (nav login state) via request.state.
    request.state.user = load_user(request)
    return await call_next(request)


def _insecure_session_config() -> bool:
    """True when we'd be signing session cookies with a throwaway key in prod.

    session_https_only is the dev/prod tell: it must be False to log in over
    local http, and stays True on Fly.
    """
    return not settings.session_secret and settings.session_https_only


def _session_secret() -> str:
    """Key that signs session cookies.

    Falls back to a *random per-process* key rather than the hardcoded string
    this used to use — that fallback meant a misconfigured production deploy
    booted happily with forgeable cookies and anyone could mint an admin
    session. A random key at least can't be guessed; _check_session_secret()
    then refuses to start the server at all when it's production posture.
    """
    if settings.session_secret:
        return settings.session_secret
    logger.warning(
        "PICLSTATS_SESSION_SECRET is empty — using a random per-process key. "
        "Sessions will not survive a restart."
    )
    return secrets.token_hex(32)


app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    https_only=settings.session_https_only,
    same_site="lax",
    # Coaches shouldn't be re-logging in mid-season; 14 days is Starlette's
    # default, set here so it reads as a decision rather than an accident.
    max_age=14 * 24 * 60 * 60,
)


@app.exception_handler(LoginRequired)
async def _login_required_handler(request: Request, exc: LoginRequired):
    from urllib.parse import quote

    return RedirectResponse(f"/login?next={quote(exc.next_path)}", status_code=303)


def _check_session_secret() -> None:
    # Refuse to serve traffic with an unset secret in production posture.
    # Raising here (rather than at import) keeps the module importable for
    # tests and tooling, while still stopping the server from coming up.
    if _insecure_session_config():
        raise RuntimeError(
            "PICLSTATS_SESSION_SECRET is not set. Generate one with "
            '`python -c "import secrets; print(secrets.token_hex(32))"`, then '
            "`flyctl secrets set PICLSTATS_SESSION_SECRET=…`. For local http dev, "
            "set PICLSTATS_SESSION_HTTPS_ONLY=false instead."
        )


def _bootstrap_admin() -> None:
    # Seed the first admin from env so a fresh deploy has a way in. No-op once
    # that account exists, so it's safe to leave the env vars set.
    if not (settings.admin_email and settings.admin_password):
        return
    from piclstats.db import users_store
    from piclstats.web.auth import hash_password

    try:
        if users_store.get_user_by_email(settings.admin_email):
            return
        users_store.create_user(
            email=settings.admin_email,
            name="Admin",
            password_hash=hash_password(settings.admin_password),
            role="admin",
        )
        logger.info("Bootstrapped admin account %s", settings.admin_email)
    except Exception:
        logger.exception("Failed to bootstrap admin account")


from piclstats.web.admin import router as admin_router  # noqa: E402
from piclstats.web.auth import router as auth_router  # noqa: E402

app.include_router(auth_router)
app.include_router(admin_router)


def _ctx(request: Request, **kwargs) -> dict:
    """Build base template context."""
    return {"request": request, **kwargs}


def optional_season(season: str = Query("")) -> int | None:
    return int(season) if season else None


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with get_session() as session:
        stats = queries.overview_stats(session)
        seasons = queries.seasons_list(session)
        top_riders = queries.leaderboard(session, limit=10)
        top_teams = queries.team_leaderboard(session, limit=10)
    return templates.TemplateResponse(
        "home.html",
        _ctx(
            request,
            stats=stats,
            seasons=seasons,
            top_riders=top_riders,
            top_teams=top_teams,
        ),
    )


@app.get("/riders", response_class=HTMLResponse)
def rider_search(
    request: Request,
    q: str = Query("", description="Rider name search"),
    team: str = Query("", description="Team filter"),
    season: int | None = Depends(optional_season),
):
    with get_session() as session:
        results = queries.search_riders(session, q, team or None, season) if q else []
        seasons = queries.seasons_list(session)
        teams = queries.teams_list(session)
    return templates.TemplateResponse(
        "riders.html",
        _ctx(
            request,
            results=results,
            q=q,
            team=team,
            season=season,
            seasons=seasons,
            teams=teams,
        ),
    )


@app.get("/rider/{rider_id}", response_class=HTMLResponse)
def rider_profile(request: Request, rider_id: int):
    with get_session() as session:
        data = queries.rider_detail(session, rider_id)
    if not data:
        return HTMLResponse("Rider not found", status_code=404)
    return templates.TemplateResponse("rider_detail.html", _ctx(request, **data))


@app.get("/teams", response_class=HTMLResponse)
def team_search(
    request: Request,
    q: str = Query("", description="Team name search"),
    season: int | None = Depends(optional_season),
):
    with get_session() as session:
        results = queries.search_teams(session, q, season) if q else []
        seasons = queries.seasons_list(session)
    return templates.TemplateResponse(
        "teams.html",
        _ctx(
            request,
            results=results,
            q=q,
            season=season,
            seasons=seasons,
        ),
    )


@app.get("/team/{team_name}", response_class=HTMLResponse)
def team_profile(
    request: Request,
    team_name: str,
    season: int | None = Depends(optional_season),
):
    with get_session() as session:
        data = queries.team_detail(session, team_name, season)
    if not data:
        return HTMLResponse("Team not found", status_code=404)
    return templates.TemplateResponse("team_detail.html", _ctx(request, **data, season=season))


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_page(
    request: Request,
    season: int | None = Depends(optional_season),
    division: str = Query(""),
    gender: str = Query(""),
    metric: str = Query("avg_points"),
    view: str = Query("riders"),
):
    with get_session() as session:
        seasons = queries.seasons_list(session)
        divisions = queries.divisions_list(session)
        if view == "teams":
            results = queries.team_leaderboard(session, season, limit=50)
        else:
            results = queries.leaderboard(
                session, season, division or None, gender or None, metric, limit=50
            )
    return templates.TemplateResponse(
        "leaderboard.html",
        _ctx(
            request,
            results=results,
            seasons=seasons,
            divisions=divisions,
            season=season,
            division=division,
            gender=gender,
            metric=metric,
            view=view,
        ),
    )


@app.get("/courses", response_class=HTMLResponse)
def courses_page(request: Request):
    with get_session() as session:
        course_list = queries.courses_list(session)
    return templates.TemplateResponse("courses.html", _ctx(request, courses=course_list))


@app.get("/course/{course_id}", response_class=HTMLResponse)
def course_profile(
    request: Request,
    course_id: int,
    season: int | None = Depends(optional_season),
):
    with get_session() as session:
        data = queries.course_detail(session, course_id, season)
    if not data:
        return HTMLResponse("Course not found", status_code=404)
    return templates.TemplateResponse("course_detail.html", _ctx(request, **data, season=season))


@app.get("/rider/{rider_id}/forecast", response_class=HTMLResponse)
def rider_forecast(
    request: Request,
    rider_id: int,
    target_division: str = Query(""),
    season: int | None = Depends(optional_season),
    _user: dict = Depends(require_member),
):
    from piclstats.web.forecast import ForecastInput, RaceObservation, StatisticalForecastModel
    from piclstats.web.staging import build_speed_rating

    with get_session() as session:
        rider_data = queries.rider_forecast_data(session, rider_id)
        if not rider_data:
            return HTMLResponse("Rider not found", status_code=404)

        # Season-to-date speed rating (z-score vs age-group field) — shown
        # alongside the division prediction regardless of selection. Never let
        # this new analytic break the existing forecast page.
        try:
            speed_rating = build_speed_rating(queries.rider_speed_rating(session, rider_id))
        except Exception:
            logger.exception("speed rating failed for rider %s", rider_id)
            speed_rating = None

        source_div = rider_data["primary_division"]
        gender = rider_data["gender"]

        if not source_div or not gender:
            return templates.TemplateResponse(
                "forecast.html",
                _ctx(
                    request,
                    rider=rider_data,
                    divisions=[],
                    target_division="",
                    forecast=None,
                    season=season,
                    speed_rating=speed_rating,
                    error="Not enough race data to forecast.",
                ),
            )

        divisions = queries.available_target_divisions(session, source_div, gender)

        forecast_result = None
        error = None

        if target_division:
            source_profile = queries.division_profile_lookup(session, source_div, gender)
            target_profile = queries.division_profile_lookup(session, target_division, gender)

            if not source_profile or not target_profile:
                error = f"Could not find division profiles for {source_div} or {target_division}"
            else:
                target_dist = queries.division_pace_distribution(
                    session, target_division, gender, season
                )

                observations = [
                    RaceObservation(
                        event_name=r["event_name"],
                        course_id=r.get("course_id"),
                        season=r["season"],
                        event_order=r.get("event_order", 0),
                        min_per_mile=r["min_per_mile"],
                        division=r["division"],
                        loop_type=r.get("loop_type"),
                        lap_count=r.get("lap_count"),
                        elevation_ft_per_mile=r.get("elevation_ft_per_mile"),
                    )
                    for r in rider_data["races"]
                    if r.get("min_per_mile") is not None
                ]

                if len(observations) < 2:
                    error = "Need at least 2 races with timing data to forecast."
                elif not target_dist["paces"]:
                    error = f"No pace data available for {target_division} {gender}."
                else:
                    inp = ForecastInput(
                        rider_id=rider_data["canonical_id"],
                        rider_name=rider_data["info"]["name"],
                        rider_gender=gender,
                        source_division=source_div,
                        target_division=target_division,
                        observations=observations,
                        target_paces=target_dist["paces"],
                        target_field_sizes=target_dist["field_sizes"],
                        source_laps=source_profile["lap_count"],
                        target_laps=target_profile["lap_count"],
                        source_loop_type=source_profile["loop_type"],
                        target_loop_type=target_profile["loop_type"],
                        source_loop_miles=source_profile["loop_miles"],
                        target_loop_miles=target_profile["loop_miles"],
                    )

                    from piclstats.db.settings_store import get_forecast_config

                    model = StatisticalForecastModel(config=get_forecast_config())
                    forecast_result = model.predict(inp)
                    if forecast_result is None:
                        error = "Not enough data to produce a reliable forecast."

    return templates.TemplateResponse(
        "forecast.html",
        _ctx(
            request,
            rider=rider_data,
            divisions=divisions,
            target_division=target_division,
            forecast=forecast_result,
            season=season,
            speed_rating=speed_rating,
            error=error if not forecast_result else None,
        ),
    )


def _staging_grid(session, age_group, gender, season, metric, sort, division, conference, wave):
    from piclstats.web import staging as staging_mod

    rows = queries.staging_rows(session, age_group, gender, season)
    return staging_mod.build_grid(
        rows,
        metric=metric,
        sort=sort,
        division=division or None,
        conference=conference or None,
        wave_size=max(1, wave),
    )


@app.get("/staging", response_class=HTMLResponse)
def staging_page(
    request: Request,
    age_group: str = Query("MS"),
    gender: str = Query("Male"),
    season: int | None = Depends(optional_season),
    metric: str = Query("pace"),
    sort: str = Query("best"),
    division: str = Query(""),
    conference: str = Query(""),
    wave: int = Query(20),
    _user: dict = Depends(require_member),
):
    with get_session() as session:
        seasons = queries.seasons_list(session)
        if season is None:
            season = seasons[-1] if seasons else None
        grid = None
        if season is not None:
            grid = _staging_grid(
                session, age_group, gender, season, metric, sort, division, conference, wave
            )
    return templates.TemplateResponse(
        "staging.html",
        _ctx(
            request,
            grid=grid,
            seasons=seasons,
            season=season,
            age_group=age_group,
            gender=gender,
            metric=metric,
            sort=sort,
            division=division,
            conference=conference,
            wave=wave,
        ),
    )


@app.get("/racechart", response_class=HTMLResponse)
def racechart_page(
    request: Request,
    event_id: int | None = Query(None),
    category: str = Query(""),
    top: int = Query(0, description="Show only the top N finishers; 0 = all"),
    _user: dict = Depends(require_member),
):
    from piclstats.web import racechart as racechart_mod

    with get_session() as session:
        events = queries.events_list(session)
        if not events:
            return templates.TemplateResponse(
                "racechart.html",
                _ctx(
                    request,
                    events=[],
                    event=None,
                    categories=[],
                    category="",
                    chart=None,
                    lap_chart=None,
                    top=top,
                ),
            )

        # Default to the most recent event with timing.
        if event_id is None or not any(e["id"] == event_id for e in events):
            selected_id: int = events[0]["id"]
        else:
            selected_id = event_id
        event = next(e for e in events if e["id"] == selected_id)

        categories = queries.event_categories(session, selected_id)
        cat_names = [c["category"] for c in categories]
        # Default to the largest field in the event (the marquee race).
        if category not in cat_names:
            category = max(categories, key=lambda c: c["field"])["category"] if categories else ""

        top_n = top if top > 0 else None
        chart = None
        lap_chart = None
        if category:
            rows = queries.event_lap_rows(session, selected_id, category)
            chart = racechart_mod.build_position_chart(rows, top=top_n)
            lap_chart = racechart_mod.build_lap_chart(rows, top=top_n)

    return templates.TemplateResponse(
        "racechart.html",
        _ctx(
            request,
            events=events,
            event=event,
            categories=categories,
            category=category,
            chart=chart,
            lap_chart=lap_chart,
            top=top,
        ),
    )


@app.get("/staging.csv")
def staging_csv(
    age_group: str = Query("MS"),
    gender: str = Query("Male"),
    season: int | None = Depends(optional_season),
    metric: str = Query("pace"),
    sort: str = Query("best"),
    division: str = Query(""),
    conference: str = Query(""),
    wave: int = Query(20),
    _user: dict = Depends(require_member_api),
):
    with get_session() as session:
        seasons = queries.seasons_list(session)
        if season is None:
            season = seasons[-1] if seasons else None
        if season is None:
            return Response("No data", media_type="text/plain")
        grid = _staging_grid(
            session, age_group, gender, season, metric, sort, division, conference, wave
        )

    buf = io.StringIO()
    w = csv.writer(buf)
    events = grid["events"]
    w.writerow(
        ["Rank", "Wave", "Name", "Team", "Conference", "Division", "Best z", "Avg z", "Races"]
        + [e["event_name"] for e in events]
    )
    for r in grid["riders"]:
        per = []
        for e in events:
            v = r["per_event"].get(e["event_id"])
            per.append("" if v is None else v)
        w.writerow(
            [
                r["rank"],
                r["wave"] or "",
                r["name"],
                r["team"] or "",
                r["conference"] or "",
                r["division"] or "",
                "" if r["best_z"] is None else r["best_z"],
                "" if r["avg_z"] is None else r["avg_z"],
                r["n_events"],
            ]
            + per
        )

    fname = f"staging_{season}_{age_group}_{gender}.csv"
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
