"""FastAPI web dashboard for PICL Stats."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from piclstats.db.engine import get_session
from piclstats.web import queries

TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="PICL Stats Dashboard")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

from piclstats.web.admin import router as admin_router  # noqa: E402
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
    return templates.TemplateResponse("home.html", _ctx(
        request, stats=stats, seasons=seasons,
        top_riders=top_riders, top_teams=top_teams,
    ))


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
    return templates.TemplateResponse("riders.html", _ctx(
        request, results=results, q=q, team=team,
        season=season, seasons=seasons, teams=teams,
    ))


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
    return templates.TemplateResponse("teams.html", _ctx(
        request, results=results, q=q, season=season, seasons=seasons,
    ))


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
    return templates.TemplateResponse("leaderboard.html", _ctx(
        request, results=results, seasons=seasons, divisions=divisions,
        season=season, division=division, gender=gender, metric=metric, view=view,
    ))


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
):
    from piclstats.web.forecast import ForecastInput, RaceObservation, StatisticalForecastModel

    with get_session() as session:
        rider_data = queries.rider_forecast_data(session, rider_id)
        if not rider_data:
            return HTMLResponse("Rider not found", status_code=404)

        source_div = rider_data["primary_division"]
        gender = rider_data["gender"]

        if not source_div or not gender:
            return templates.TemplateResponse("forecast.html", _ctx(
                request, rider=rider_data, divisions=[], target_division="",
                forecast=None, season=season, error="Not enough race data to forecast.",
            ))

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

    return templates.TemplateResponse("forecast.html", _ctx(
        request, rider=rider_data, divisions=divisions,
        target_division=target_division, forecast=forecast_result,
        season=season, error=error if not forecast_result else None,
    ))
