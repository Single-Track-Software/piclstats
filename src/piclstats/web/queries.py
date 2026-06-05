"""SQL queries powering the dashboard."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session


def _serialize(row) -> dict:
    """Convert a RowMapping to a plain dict with JSON-safe values."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, timedelta):
            total = int(v.total_seconds())
            hours, rem = divmod(total, 3600)
            minutes, seconds = divmod(rem, 60)
            d[k] = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        elif isinstance(v, Decimal):
            d[k] = float(v)
    return d


# Common CTE fragment: resolve any rider to its canonical ID
_CANONICAL_CTE = """
    canonical AS (
        SELECT ri.id AS rider_id, COALESCE(ra.canonical_id, ri.id) AS cid,
               ri.name, ri.team, ri.school
        FROM riders ri
        LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
    )
"""

# SQL fragments for deriving pace from actual completed laps rather than the
# generic division_laps.lap_count (which is often wrong for state championships
# and any event where the declared lap count diverged from the field's real
# laps). Paired with a consistency gate: only trust the result if the sum of
# recorded lap splits matches total_time within 10 seconds.
_ACTUAL_LAPS = """
    (CASE WHEN r.lap1 IS NOT NULL THEN 1 ELSE 0 END
   + CASE WHEN r.lap2 IS NOT NULL THEN 1 ELSE 0 END
   + CASE WHEN r.lap3 IS NOT NULL THEN 1 ELSE 0 END
   + CASE WHEN r.lap4 IS NOT NULL THEN 1 ELSE 0 END
   + CASE WHEN r.lap5 IS NOT NULL THEN 1 ELSE 0 END
   + CASE WHEN r.lap6 IS NOT NULL THEN 1 ELSE 0 END)
"""
_SUM_LAPS = """
    (COALESCE(r.lap1,'0'::interval) + COALESCE(r.lap2,'0'::interval)
   + COALESCE(r.lap3,'0'::interval) + COALESCE(r.lap4,'0'::interval)
   + COALESCE(r.lap5,'0'::interval) + COALESCE(r.lap6,'0'::interval))
"""
_LAPS_CONSISTENT = f"""
    ({_ACTUAL_LAPS} > 0
     AND abs(EXTRACT(EPOCH FROM (r.total_time - {_SUM_LAPS}))) < 10)
"""

# Sanity guardrail: MTB pace outside this range is physiologically implausible
# and usually signals bad loop distance data (e.g. rally events on short tracks
# where the default 2.0/3.5 mi loop distance doesn't apply).
_PACE_MIN = 3.5
_PACE_MAX = 15.0

# Only points events count toward standings. Rallies and exhibitions are
# excluded from every points/place aggregate and ranking below; they still
# appear in individual race history (badged non-scoring). Assumes the events
# table is joined with alias `e`.
_POINTS_ONLY = "e.event_type = 'points'"


def overview_stats(session: Session) -> dict:
    row = session.execute(text(f"""
        WITH {_CANONICAL_CTE}
        SELECT
            count(DISTINCT e.id) AS events,
            count(DISTINCT c.cid) AS riders,
            count(DISTINCT c.team) AS teams,
            count(r.id) AS results,
            count(DISTINCT e.season) AS seasons
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN canonical c ON c.rider_id = r.rider_id
    """)).one()
    return _serialize(row._mapping)


def seasons_list(session: Session) -> list[int]:
    rows = session.execute(text(
        "SELECT DISTINCT season FROM events ORDER BY season"
    )).all()
    return [r[0] for r in rows]


def divisions_list(session: Session) -> list[str]:
    # Only divisions that appear in scoring events, so exhibition-only labels
    # (e.g. 'Advanced' from a short-track exhibition) don't show as filter
    # options that would return no results.
    rows = session.execute(text(
        "SELECT DISTINCT r.division "
        "FROM results r JOIN events e ON r.event_id = e.id "
        "WHERE r.division IS NOT NULL AND e.event_type = 'points' "
        "ORDER BY r.division"
    )).all()
    return [r[0] for r in rows]


def teams_list(session: Session) -> list[str]:
    rows = session.execute(text(
        "SELECT DISTINCT team FROM riders WHERE team IS NOT NULL ORDER BY team"
    )).all()
    return [r[0] for r in rows]


def search_riders(
    session: Session, q: str, team: str | None = None, season: int | None = None
) -> list[dict]:
    """Search riders by name. Merged riders appear as one row."""
    sql = f"""
        WITH {_CANONICAL_CTE}
        SELECT
            c.cid AS id,
            c.name,
            string_agg(DISTINCT c.team, ' / ' ORDER BY c.team) AS team,
            count(DISTINCT r.event_id) AS race_count,
            count(DISTINCT e.season) AS seasons_active,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place,
            min(e.season) AS first_season,
            max(e.season) AS last_season
        FROM canonical c
        JOIN results r ON r.rider_id = c.rider_id
        JOIN events e ON r.event_id = e.id
        WHERE c.name ILIKE :q
          AND {_POINTS_ONLY}
    """
    params: dict = {"q": f"%{q}%"}
    if team:
        sql += " AND c.team ILIKE :team"
        params["team"] = f"%{team}%"
    if season:
        sql += " AND e.season = :season"
        params["season"] = season
    sql += """
        GROUP BY c.cid, c.name
        ORDER BY avg_points DESC NULLS LAST, c.name
        LIMIT 100
    """
    rows = session.execute(text(sql), params).all()
    return [_serialize(r._mapping) for r in rows]


def rider_detail(session: Session, rider_id: int) -> dict | None:
    """Full rider profile — unified across all aliases."""
    # Resolve to canonical
    canonical_id = session.execute(text("""
        SELECT COALESCE(
            (SELECT canonical_id FROM rider_aliases WHERE rider_id = :id),
            :id
        )
    """), {"id": rider_id}).scalar()

    # Get all rider_ids in this canonical group
    group_ids = session.execute(text("""
        SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
        UNION
        SELECT :cid
    """), {"cid": canonical_id}).all()
    all_ids = [r[0] for r in group_ids]

    info = session.execute(text("""
        SELECT ri.id, ri.name, ri.school,
               string_agg(DISTINCT ri2.team, ' / ' ORDER BY ri2.team) AS team
        FROM riders ri
        CROSS JOIN riders ri2
        WHERE ri.id = :cid AND ri2.id = ANY(:ids)
        GROUP BY ri.id, ri.name, ri.school
    """), {"cid": canonical_id, "ids": all_ids}).one_or_none()
    if not info:
        return None

    # Team history
    team_history = session.execute(text("""
        SELECT DISTINCT ri.team, min(e.season) AS from_season, max(e.season) AS to_season,
               count(r.id) AS races
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.id = ANY(:ids)
        GROUP BY ri.team
        ORDER BY from_season
    """), {"ids": all_ids}).all()

    races = session.execute(text(f"""
        SELECT
            e.season,
            e.event_name,
            e.event_order,
            e.event_type,
            r.category,
            r.division,
            r.gender,
            r.place,
            r.points,
            r.status,
            r.conference,
            r.total_time,
            r.total_time_raw,
            r.lap1, r.lap2, r.lap3, r.lap4, r.lap5, r.lap6,
            r.penalty,
            ri.team,
            dl.loop_type,
            dl.lap_count AS expected_laps,
            cl.distance_miles AS loop_distance,
            CASE WHEN r.total_time IS NOT NULL
                      AND r.total_time < interval '2 hours'
                      AND cl.distance_miles > 0
                      AND {_LAPS_CONSISTENT}
                 THEN round((
                     (EXTRACT(EPOCH FROM r.total_time) / 60.0)
                     / ({_ACTUAL_LAPS} * cl.distance_miles)
                 )::numeric, 1)
            END AS min_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN riders ri ON r.rider_id = ri.id
        LEFT JOIN division_laps dl ON dl.course_id = e.course_id
            AND dl.division = r.division
            AND (dl.gender = r.gender OR (dl.gender IS NULL AND r.gender IS NULL))
            AND dl.season IS NULL
            AND dl.loop_type IS NOT NULL
        LEFT JOIN course_loops cl ON cl.course_id = e.course_id
            AND cl.loop_type = dl.loop_type
        WHERE r.rider_id = ANY(:ids)
        ORDER BY e.season, e.event_order
    """), {"ids": all_ids}).all()

    season_stats = session.execute(text("""
        SELECT
            e.season,
            count(*) AS races,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place,
            min(r.place) AS best_place,
            max(r.points) AS best_points,
            sum(r.points) AS total_points,
            r.division AS primary_division
        FROM results r
        JOIN events e ON r.event_id = e.id
        WHERE r.rider_id = ANY(:ids) AND r.place IS NOT NULL
          AND e.event_type = 'points'
        GROUP BY e.season, r.division
        ORDER BY e.season
    """), {"ids": all_ids}).all()

    percentiles = session.execute(text("""
        WITH ranked AS (
            SELECT
                r.event_id,
                r.rider_id,
                r.category,
                r.place,
                count(*) OVER (PARTITION BY r.event_id, r.category) AS field_size,
                r.place::numeric / NULLIF(count(*) OVER (PARTITION BY r.event_id, r.category), 0) AS pct_rank
            FROM results r
            WHERE r.place IS NOT NULL
        )
        SELECT
            e.season,
            e.event_name,
            e.event_order,
            ranked.category,
            ranked.place,
            ranked.field_size,
            round(((1.0 - ranked.pct_rank) * 100)::numeric, 1) AS percentile
        FROM ranked
        JOIN events e ON ranked.event_id = e.id
        WHERE ranked.rider_id = ANY(:ids)
        ORDER BY e.season, e.event_order
    """), {"ids": all_ids}).all()

    return {
        "info": _serialize(info._mapping),
        "team_history": [_serialize(r._mapping) for r in team_history],
        "races": [_serialize(r._mapping) for r in races],
        "season_stats": [_serialize(r._mapping) for r in season_stats],
        "percentiles": [_serialize(r._mapping) for r in percentiles],
    }


def search_teams(session: Session, q: str, season: int | None = None) -> list[dict]:
    sql = """
        SELECT
            ri.team,
            count(DISTINCT ri.id) AS rider_count,
            count(DISTINCT r.event_id) AS race_count,
            count(DISTINCT e.season) AS seasons_active,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.team ILIKE :q
          AND e.event_type = 'points'
    """
    params: dict = {"q": f"%{q}%"}
    if season:
        sql += " AND e.season = :season"
        params["season"] = season
    sql += """
        GROUP BY ri.team
        ORDER BY avg_points DESC NULLS LAST
        LIMIT 50
    """
    rows = session.execute(text(sql), params).all()
    return [_serialize(r._mapping) for r in rows]


def team_detail(session: Session, team_name: str, season: int | None = None) -> dict | None:
    params: dict = {"team": team_name}
    season_filter = ""
    if season:
        season_filter = "AND e.season = :season"
        params["season"] = season

    # Use canonical IDs so riders who changed teams still show their full stats
    # when viewing from any of their teams
    roster = session.execute(text(f"""
        WITH {_CANONICAL_CTE}
        SELECT
            c.cid AS id,
            c.name,
            r.division,
            r.gender,
            count(DISTINCT r.event_id) AS races,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place,
            min(r.place) AS best_place,
            max(r.points) AS best_points,
            sum(r.points) AS total_points
        FROM canonical c
        JOIN results r ON r.rider_id = c.rider_id
        JOIN events e ON r.event_id = e.id
        WHERE c.team = :team {season_filter}
          AND r.place IS NOT NULL
          AND {_POINTS_ONLY}
        GROUP BY c.cid, c.name, r.division, r.gender
        ORDER BY r.division, avg_points DESC NULLS LAST
    """), params).all()

    division_summary = session.execute(text(f"""
        SELECT
            r.division,
            r.gender,
            count(DISTINCT ri.id) AS riders,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.team = :team {season_filter}
          AND r.place IS NOT NULL
          AND {_POINTS_ONLY}
        GROUP BY r.division, r.gender
        ORDER BY r.division, r.gender
    """), params).all()

    event_performance = session.execute(text(f"""
        SELECT
            e.season,
            e.event_name,
            e.event_order,
            count(DISTINCT ri.id) AS riders,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place,
            sum(r.points) AS total_points
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.team = :team {season_filter}
        GROUP BY e.season, e.event_name, e.event_order, e.id
        ORDER BY e.season, e.event_order
    """), params).all()

    seasons_available = session.execute(text("""
        SELECT DISTINCT e.season
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.team = :team
        ORDER BY e.season
    """), {"team": team_name}).all()

    return {
        "team_name": team_name,
        "roster": [_serialize(r._mapping) for r in roster],
        "division_summary": [_serialize(r._mapping) for r in division_summary],
        "event_performance": [_serialize(r._mapping) for r in event_performance],
        "seasons_available": [r[0] for r in seasons_available],
    }


def leaderboard(
    session: Session,
    season: int | None = None,
    division: str | None = None,
    gender: str | None = None,
    metric: str = "avg_points",
    limit: int = 25,
) -> list[dict]:
    """Top riders by chosen metric — merged riders unified."""
    params: dict = {}
    filters: list[str] = ["r.place IS NOT NULL", _POINTS_ONLY]
    if season:
        filters.append("e.season = :season")
        params["season"] = season
    if division:
        filters.append("r.division = :division")
        params["division"] = division
    if gender:
        filters.append("r.gender = :gender")
        params["gender"] = gender

    where = " AND ".join(filters)

    order_col = {
        "avg_points": "avg_points DESC NULLS LAST",
        "avg_place": "avg_place ASC NULLS LAST",
        "total_points": "total_points DESC NULLS LAST",
        "races": "races DESC",
        "best_place": "best_place ASC NULLS LAST",
    }.get(metric, "avg_points DESC NULLS LAST")

    sql = f"""
        WITH {_CANONICAL_CTE}
        SELECT
            c.cid AS rider_id,
            c.name,
            string_agg(DISTINCT c.team, ' / ' ORDER BY c.team) AS team,
            r.division,
            r.gender,
            count(DISTINCT r.event_id) AS races,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place,
            min(r.place) AS best_place,
            sum(r.points) AS total_points
        FROM results r
        JOIN canonical c ON c.rider_id = r.rider_id
        JOIN events e ON r.event_id = e.id
        WHERE {where}
        GROUP BY c.cid, c.name, r.division, r.gender
        HAVING count(DISTINCT r.event_id) >= 2
        ORDER BY {order_col}
        LIMIT :limit
    """
    params["limit"] = limit
    rows = session.execute(text(sql), params).all()
    return [_serialize(r._mapping) for r in rows]


def team_leaderboard(
    session: Session,
    season: int | None = None,
    limit: int = 25,
) -> list[dict]:
    params: dict = {"limit": limit}
    season_filter = ""
    if season:
        season_filter = "AND e.season = :season"
        params["season"] = season

    sql = f"""
        SELECT
            ri.team,
            count(DISTINCT ri.id) AS riders,
            count(DISTINCT r.event_id) AS races,
            round(avg(r.points)::numeric, 1) AS avg_points,
            round(avg(r.place)::numeric, 1) AS avg_place,
            sum(r.points) AS total_points,
            min(r.place) AS best_place
        FROM results r
        JOIN riders ri ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE r.place IS NOT NULL AND ri.team IS NOT NULL
          AND {_POINTS_ONLY}
          {season_filter}
        GROUP BY ri.team
        HAVING count(DISTINCT ri.id) >= 3
        ORDER BY avg_points DESC NULLS LAST
        LIMIT :limit
    """
    rows = session.execute(text(sql), params).all()
    return [_serialize(r._mapping) for r in rows]


# ── Course queries ──────────────────────────────────────────────────


def courses_list(session: Session) -> list[dict]:
    rows = session.execute(text("""
        SELECT c.id, c.name, c.location, c.distance_miles, c.elevation_ft,
               c.difficulty_score, count(DISTINCT e.id) AS event_count,
               count(r.id) AS result_count
        FROM courses c
        LEFT JOIN events e ON e.course_id = c.id
        LEFT JOIN results r ON r.event_id = e.id
        GROUP BY c.id
        ORDER BY c.name
    """)).all()
    return [_serialize(r._mapping) for r in rows]


def course_detail(session: Session, course_id: int, season: int | None = None) -> dict | None:
    info = session.execute(text("""
        SELECT id, name, location, difficulty_score, notes
        FROM courses WHERE id = :id
    """), {"id": course_id}).one_or_none()
    if not info:
        return None

    loops = session.execute(text("""
        SELECT loop_type, distance_miles, elevation_ft
        FROM course_loops WHERE course_id = :id
        ORDER BY loop_type
    """), {"id": course_id}).all()

    params: dict = {"cid": course_id}
    season_filter = ""
    if season:
        season_filter = "AND e.season = :season"
        params["season"] = season

    events_at = session.execute(text(f"""
        SELECT e.id, e.season, e.event_order, e.event_name,
               count(r.id) AS results
        FROM events e
        LEFT JOIN results r ON r.event_id = e.id
        WHERE e.course_id = :cid {season_filter}
        GROUP BY e.id
        ORDER BY e.season, e.event_order
    """), params).all()

    laps = session.execute(text("""
        SELECT division, gender, lap_count, max_duration_mins, cutoff_mins
        FROM division_laps
        WHERE course_id = :cid AND season IS NULL
        ORDER BY lap_count DESC, division, gender
    """), {"cid": course_id}).all()

    division_stats = session.execute(text(f"""
        SELECT r.division, r.gender,
               dl.loop_type,
               dl.lap_count,
               cl.distance_miles AS loop_distance,
               count(DISTINCT COALESCE(ra.canonical_id, ri.id)) AS riders,
               count(r.id) AS results,
               round(avg(r.place)::numeric, 1) AS avg_place,
               round(avg(r.points)::numeric, 1) AS avg_points,
               round(avg(EXTRACT(EPOCH FROM r.total_time))::numeric, 1) AS avg_time_secs,
               min(r.total_time) AS fastest_time,
               round(avg(
                   EXTRACT(EPOCH FROM r.total_time) / NULLIF({_ACTUAL_LAPS}, 0)
               ) FILTER (WHERE {_LAPS_CONSISTENT})::numeric, 1) AS avg_pace_per_lap_secs,
               round(avg(
                   (EXTRACT(EPOCH FROM r.total_time) / 60.0)
                   / NULLIF({_ACTUAL_LAPS} * cl.distance_miles, 0)
               ) FILTER (WHERE {_LAPS_CONSISTENT} AND cl.distance_miles > 0)::numeric, 1) AS avg_min_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN riders ri ON r.rider_id = ri.id
        LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
        LEFT JOIN division_laps dl ON dl.course_id = e.course_id
            AND dl.division = r.division
            AND (dl.gender = r.gender OR (dl.gender IS NULL AND r.gender IS NULL))
            AND dl.season IS NULL
            AND dl.loop_type IS NOT NULL
        LEFT JOIN course_loops cl ON cl.course_id = e.course_id
            AND cl.loop_type = dl.loop_type
        WHERE e.course_id = :cid AND r.place IS NOT NULL AND r.total_time IS NOT NULL
          AND r.total_time < interval '2 hours'
          {season_filter}
        GROUP BY r.division, r.gender, dl.loop_type, dl.lap_count, cl.distance_miles
        ORDER BY r.division, r.gender
    """), params).all()

    top_riders = session.execute(text(f"""
        WITH {_CANONICAL_CTE}
        SELECT
            c.cid AS rider_id,
            c.name,
            string_agg(DISTINCT c.team, ' / ' ORDER BY c.team) AS team,
            r.division,
            count(DISTINCT e.id) AS appearances,
            round(avg(r.place)::numeric, 1) AS avg_place,
            round(avg(r.points)::numeric, 1) AS avg_points,
            min(r.total_time) AS best_time
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN canonical c ON c.rider_id = r.rider_id
        WHERE e.course_id = :cid AND r.place IS NOT NULL
          {season_filter}
        GROUP BY c.cid, c.name, r.division
        HAVING count(DISTINCT e.id) >= 2
        ORDER BY avg_points DESC NULLS LAST
        LIMIT 20
    """), params).all()

    seasons_available = session.execute(text("""
        SELECT DISTINCT e.season
        FROM events e WHERE e.course_id = :cid
        ORDER BY e.season
    """), {"cid": course_id}).all()

    loops_dict = {r[0]: {"distance_miles": r[1], "elevation_ft": r[2]} for r in loops}

    return {
        "info": _serialize(info._mapping),
        "loops": loops_dict,
        "events": [_serialize(r._mapping) for r in events_at],
        "laps": [_serialize(r._mapping) for r in laps],
        "division_stats": [_serialize(r._mapping) for r in division_stats],
        "top_riders": [_serialize(r._mapping) for r in top_riders],
        "seasons_available": [r[0] for r in seasons_available],
    }


# ── Forecast queries ────────────────────────────────────────────────


def rider_forecast_data(session: Session, rider_id: int) -> dict | None:
    """Get rider info + min/mile per race for forecasting."""
    # Resolve canonical
    canonical_id = session.execute(text("""
        SELECT COALESCE(
            (SELECT canonical_id FROM rider_aliases WHERE rider_id = :id),
            :id
        )
    """), {"id": rider_id}).scalar()

    group_ids = session.execute(text("""
        SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
        UNION SELECT :cid
    """), {"cid": canonical_id}).all()
    all_ids = [r[0] for r in group_ids]

    info = session.execute(text("""
        SELECT ri.id, ri.name,
               string_agg(DISTINCT ri2.team, ' / ' ORDER BY ri2.team) AS team
        FROM riders ri
        CROSS JOIN riders ri2
        WHERE ri.id = :cid AND ri2.id = ANY(:ids)
        GROUP BY ri.id, ri.name
    """), {"cid": canonical_id, "ids": all_ids}).one_or_none()
    if not info:
        return None

    races = session.execute(text(f"""
        SELECT
            e.event_name,
            e.course_id,
            e.season,
            e.event_order,
            r.division,
            r.gender,
            dl.loop_type,
            dl.lap_count,
            cl.distance_miles AS loop_distance,
            CASE WHEN r.total_time IS NOT NULL
                      AND r.total_time < interval '2 hours'
                      AND cl.distance_miles > 0
                      AND {_LAPS_CONSISTENT}
                 THEN round((
                     (EXTRACT(EPOCH FROM r.total_time) / 60.0)
                     / ({_ACTUAL_LAPS} * cl.distance_miles)
                 )::numeric, 1)
            END AS min_per_mile,
            CASE WHEN cl.distance_miles > 0 AND cl.elevation_ft IS NOT NULL
                 THEN round((cl.elevation_ft / cl.distance_miles)::numeric, 1)
            END AS elevation_ft_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        LEFT JOIN division_laps dl ON dl.course_id = e.course_id
            AND dl.division = r.division
            AND (dl.gender = r.gender OR (dl.gender IS NULL AND r.gender IS NULL))
            AND dl.season IS NULL AND dl.loop_type IS NOT NULL
        LEFT JOIN course_loops cl ON cl.course_id = e.course_id
            AND cl.loop_type = dl.loop_type
        WHERE r.rider_id = ANY(:ids)
          AND r.place IS NOT NULL
          AND r.status = 'OK'
        ORDER BY e.season, e.event_order
    """), {"ids": all_ids}).all()

    # Determine primary division and gender (most recent). Apply the pace
    # sanity range so rally/short-track events with bad loop distances don't
    # contaminate the rider's baseline.
    valid_races = [
        r for r in races
        if r.min_per_mile is not None
        and _PACE_MIN <= float(r.min_per_mile) <= _PACE_MAX
    ]
    primary_division = valid_races[-1].division if valid_races else None
    gender = valid_races[-1].gender if valid_races else None

    # Null out pace on races outside the sanity range so forecast observations
    # built from this list never include rally/short-track outliers.
    serialized_races = []
    for r in races:
        row = _serialize(r._mapping)
        mpm = row.get("min_per_mile")
        if mpm is not None and not (_PACE_MIN <= float(mpm) <= _PACE_MAX):
            row["min_per_mile"] = None
        serialized_races.append(row)

    return {
        "info": _serialize(info._mapping),
        "canonical_id": canonical_id,
        "races": serialized_races,
        "primary_division": primary_division,
        "gender": gender,
    }


def rider_speed_rating(session: Session, rider_id: int, min_field: int = 8) -> list[dict]:
    """Per-event z-scores for a rider vs their age-group + gender field.

    Age group = division_laps.loop_type (MS/HS). For each event the rider raced,
    we compute the field's mean/stdev (over every rider in that event + age group
    + gender) and the rider's z for both metrics: raw lap time (PICL-exact) and
    course-normalized min/mile. Lower = faster, so a fast rider has a negative z.
    Field must have >= min_field timed riders for a z to be trusted.
    """
    canonical_id = session.execute(text("""
        SELECT COALESCE(
            (SELECT canonical_id FROM rider_aliases WHERE rider_id = :id),
            :id
        )
    """), {"id": rider_id}).scalar()

    group_ids = session.execute(text("""
        SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
        UNION SELECT :cid
    """), {"cid": canonical_id}).all()
    all_ids = [r[0] for r in group_ids]

    rows = session.execute(text(f"""
        WITH base AS (
            SELECT
                e.id AS event_id, e.season, e.event_order, e.event_name,
                dl.loop_type AS age_group, r.gender, r.division,
                COALESCE(ra.canonical_id, ri.id) AS canonical_id,
                CASE WHEN r.total_time IS NOT NULL
                          AND r.total_time < interval '2 hours'
                          AND {_LAPS_CONSISTENT}
                     THEN EXTRACT(EPOCH FROM r.total_time) / NULLIF({_ACTUAL_LAPS}, 0)
                END AS lap_secs,
                CASE WHEN r.total_time IS NOT NULL
                          AND r.total_time < interval '2 hours'
                          AND cl.distance_miles > 0
                          AND {_LAPS_CONSISTENT}
                     THEN (EXTRACT(EPOCH FROM r.total_time) / 60.0)
                          / ({_ACTUAL_LAPS} * cl.distance_miles)
                END AS min_per_mile
            FROM results r
            JOIN events e ON r.event_id = e.id AND e.event_type = 'points'
            JOIN riders ri ON r.rider_id = ri.id
            LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
            JOIN division_laps dl ON dl.course_id = e.course_id
                AND dl.division = r.division
                AND (dl.gender = r.gender OR (dl.gender IS NULL AND r.gender IS NULL))
                AND dl.season IS NULL AND dl.loop_type IS NOT NULL
            LEFT JOIN course_loops cl ON cl.course_id = e.course_id
                AND cl.loop_type = dl.loop_type
            WHERE r.status = 'OK' AND r.place IS NOT NULL
              AND e.id IN (SELECT event_id FROM results WHERE rider_id = ANY(:ids))
        ),
        clean AS (
            -- Drop physiologically implausible pace (bad loop distance) so it
            -- can't skew the field mean/stdev.
            SELECT *,
                CASE WHEN min_per_mile BETWEEN {_PACE_MIN} AND {_PACE_MAX}
                     THEN min_per_mile END AS pace_ok
            FROM base
        ),
        z AS (
            SELECT *,
                avg(lap_secs) OVER w AS lap_mean,
                stddev_samp(lap_secs) OVER w AS lap_std,
                count(lap_secs) OVER w AS lap_field,
                avg(pace_ok) OVER w AS pace_mean,
                stddev_samp(pace_ok) OVER w AS pace_std,
                count(pace_ok) OVER w AS pace_field
            FROM clean
            WINDOW w AS (PARTITION BY event_id, age_group, gender)
        )
        SELECT event_name, season, event_order, age_group, gender, division,
               lap_field, pace_field,
               CASE WHEN lap_std > 0 AND lap_field >= :minf
                    THEN round(((lap_secs - lap_mean) / lap_std)::numeric, 2) END AS z_lap,
               CASE WHEN pace_std > 0 AND pace_field >= :minf
                    THEN round(((pace_ok - pace_mean) / pace_std)::numeric, 2) END AS z_pace
        FROM z
        WHERE canonical_id = :cid
        ORDER BY season, event_order
    """), {"ids": all_ids, "cid": canonical_id, "minf": min_field}).all()

    return [_serialize(r._mapping) for r in rows]


def division_pace_distribution(
    session: Session, division: str, gender: str, season: int | None = None
) -> dict:
    """Get min/mile distribution and field sizes for a division."""
    params: dict = {"division": division, "gender": gender}
    season_filter = ""
    if season:
        season_filter = "AND e.season = :season"
        params["season"] = season

    # Handle MS Advanced / Middle School Advanced equivalence
    div_filter = "r.division = :division"
    if division in ("MS Advanced", "Middle School Advanced"):
        div_filter = "r.division IN ('MS Advanced', 'Middle School Advanced')"

    rows = session.execute(text(f"""
        SELECT
            r.place,
            count(*) OVER (PARTITION BY r.event_id) AS field_size,
            round((
                (EXTRACT(EPOCH FROM r.total_time) / 60.0)
                / NULLIF({_ACTUAL_LAPS} * cl.distance_miles, 0)
            )::numeric, 1) AS min_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        LEFT JOIN division_laps dl ON dl.course_id = e.course_id
            AND dl.division = r.division
            AND (dl.gender = r.gender OR (dl.gender IS NULL AND r.gender IS NULL))
            AND dl.season IS NULL AND dl.loop_type IS NOT NULL
        LEFT JOIN course_loops cl ON cl.course_id = e.course_id
            AND cl.loop_type = dl.loop_type
        WHERE {div_filter}
          AND r.gender = :gender
          AND r.place IS NOT NULL
          AND r.status = 'OK'
          AND r.total_time IS NOT NULL
          AND r.total_time < interval '2 hours'
          AND cl.distance_miles > 0
          AND {_LAPS_CONSISTENT}
          {season_filter}
        ORDER BY min_per_mile
    """), params).all()

    paces = [
        float(r[2]) for r in rows
        if r[2] is not None and _PACE_MIN <= float(r[2]) <= _PACE_MAX
    ]
    field_sizes = list({r[1] for r in rows if r[1]})

    return {"paces": paces, "field_sizes": field_sizes}


def division_profile_lookup(
    session: Session, division: str, gender: str
) -> dict | None:
    """Get lap count, loop type, and distance for a division."""
    div_filter = "dl.division = :division"
    params: dict = {"division": division, "gender": gender}
    if division in ("MS Advanced", "Middle School Advanced"):
        div_filter = "dl.division IN ('MS Advanced', 'Middle School Advanced')"

    row = session.execute(text(f"""
        SELECT DISTINCT dl.lap_count, dl.loop_type, cl.distance_miles
        FROM division_laps dl
        JOIN course_loops cl ON cl.course_id = dl.course_id AND cl.loop_type = dl.loop_type
        WHERE {div_filter}
          AND (dl.gender = :gender OR (dl.gender IS NULL AND :gender IS NULL))
          AND dl.season IS NULL
        LIMIT 1
    """), params).one_or_none()

    if not row:
        return None
    return {"lap_count": row[0], "loop_type": row[1], "loop_miles": float(row[2])}


def available_target_divisions(
    session: Session, source_division: str, gender: str
) -> list[str]:
    """Get divisions a rider could be forecast into (same gender, exclude source and single-lap)."""
    rows = session.execute(text("""
        SELECT DISTINCT r.division
        FROM results r
        WHERE r.gender = :gender
          AND r.division != :source
          AND r.division NOT LIKE 'Single Lap%%'
          AND r.division != '9th Grade'
          AND r.place IS NOT NULL
        ORDER BY r.division
    """), {"gender": gender, "source": source_division}).all()
    return [r[0] for r in rows]
