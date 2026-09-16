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
# Riding time: the published total includes any time penalty (a 5:00 penalty
# shows as total = laps + 5:00 on raceresult), so pace and the split check use
# total minus penalty. Otherwise every penalised rider failed the check below
# and silently dropped out of pace, staging, and forecasts.
_RIDE_TIME = "(r.total_time - COALESCE(r.penalty, '0'::interval))"
_RIDE_SECS = f"EXTRACT(EPOCH FROM {_RIDE_TIME})"
_LAPS_CONSISTENT = f"""
    ({_ACTUAL_LAPS} > 0
     AND abs(EXTRACT(EPOCH FROM ({_RIDE_TIME} - {_SUM_LAPS}))) < 10)
"""


def _lap_joins(*, inner: bool = False, loop_filter: str = "") -> str:
    """Join the lap profile (dl) and loop (cl) for a result row `r` at event `e`.

    Course profiles are per season: a division_laps / course_loops row whose
    season matches the event wins, otherwise the season-NULL default applies.
    `inner` drops results with no profile at all (staging needs that);
    `loop_filter` is extra SQL against alias `d` (e.g. an age-group filter).
    """
    kind = "JOIN" if inner else "LEFT JOIN"
    return f"""
        {kind} LATERAL (
            SELECT d.lap_count, d.loop_type, d.max_duration_mins, d.cutoff_mins, d.season
            FROM division_laps d
            WHERE d.course_id = e.course_id
              AND d.division = r.division
              AND d.gender IS NOT DISTINCT FROM r.gender
              AND (d.season = e.season OR d.season IS NULL)
              AND d.loop_type IS NOT NULL
              {loop_filter}
            ORDER BY d.season NULLS LAST
            LIMIT 1
        ) dl ON true
        LEFT JOIN LATERAL (
            SELECT l.distance_miles, l.elevation_ft, l.season
            FROM course_loops l
            WHERE l.course_id = e.course_id
              AND l.loop_type = dl.loop_type
              AND (l.season = e.season OR l.season IS NULL)
            ORDER BY l.season NULLS LAST
            LIMIT 1
        ) cl ON true
    """


_LAP_JOINS = _lap_joins()
_LAP_JOINS_INNER = _lap_joins(inner=True)
_LAP_JOINS_AGE_GROUP = _lap_joins(inner=True, loop_filter="AND d.loop_type = :age_group")

# Results at one course with `full_laps` = the most laps anyone recorded in
# the same event, division and gender. PICL still places (and scores) riders
# pulled at the cutoff after fewer laps, so "fastest time" must exclude them or
# a one-lap finisher shows up as the course record. A window function does
# this in one pass — a correlated subquery here re-scanned results per row and
# took the production database down (2026-09-15).
_COURSE_RESULTS = f"""
    (
        SELECT r.*,
               max({_ACTUAL_LAPS}) OVER (PARTITION BY r.event_id, r.division, r.gender)
                   AS full_laps
        FROM results r
        JOIN events ce ON ce.id = r.event_id
        WHERE ce.course_id = :cid
    ) r
"""
_FULL_DISTANCE = f"{_ACTUAL_LAPS} = r.full_laps"

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
    row = session.execute(
        text(f"""
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
    """)
    ).one()
    return _serialize(row._mapping)


def seasons_list(session: Session) -> list[int]:
    rows = session.execute(text("SELECT DISTINCT season FROM events ORDER BY season")).all()
    return [r[0] for r in rows]


def divisions_list(session: Session) -> list[str]:
    # Only divisions that appear in scoring events, so exhibition-only labels
    # (e.g. 'Advanced' from a short-track exhibition) don't show as filter
    # options that would return no results.
    rows = session.execute(
        text(
            "SELECT DISTINCT r.division "
            "FROM results r JOIN events e ON r.event_id = e.id "
            "WHERE r.division IS NOT NULL AND e.event_type = 'points' "
            "ORDER BY r.division"
        )
    ).all()
    return [r[0] for r in rows]


def teams_list(session: Session) -> list[str]:
    rows = session.execute(
        text("SELECT DISTINCT team FROM riders WHERE team IS NOT NULL ORDER BY team")
    ).all()
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
    canonical_id = session.execute(
        text("""
        SELECT COALESCE(
            (SELECT canonical_id FROM rider_aliases WHERE rider_id = :id),
            :id
        )
    """),
        {"id": rider_id},
    ).scalar()

    # Get all rider_ids in this canonical group
    group_ids = session.execute(
        text("""
        SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
        UNION
        SELECT :cid
    """),
        {"cid": canonical_id},
    ).all()
    all_ids = [r[0] for r in group_ids]

    info = session.execute(
        text("""
        SELECT ri.id, ri.name, ri.school,
               string_agg(DISTINCT ri2.team, ' / ' ORDER BY ri2.team) AS team
        FROM riders ri
        CROSS JOIN riders ri2
        WHERE ri.id = :cid AND ri2.id = ANY(:ids)
        GROUP BY ri.id, ri.name, ri.school
    """),
        {"cid": canonical_id, "ids": all_ids},
    ).one_or_none()
    if not info:
        return None

    # Team history
    team_history = session.execute(
        text("""
        SELECT DISTINCT ri.team, min(e.season) AS from_season, max(e.season) AS to_season,
               count(r.id) AS races
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.id = ANY(:ids)
        GROUP BY ri.team
        ORDER BY from_season
    """),
        {"ids": all_ids},
    ).all()

    races = session.execute(
        text(f"""
        SELECT
            e.id AS event_id,
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
                     ({_RIDE_SECS} / 60.0)
                     / ({_ACTUAL_LAPS} * cl.distance_miles)
                 )::numeric, 1)
            END AS min_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN riders ri ON r.rider_id = ri.id
        {_LAP_JOINS}
        WHERE r.rider_id = ANY(:ids)
        ORDER BY e.season, e.event_order
    """),
        {"ids": all_ids},
    ).all()

    season_stats = session.execute(
        text("""
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
    """),
        {"ids": all_ids},
    ).all()

    percentiles = session.execute(
        text("""
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
    """),
        {"ids": all_ids},
    ).all()

    return {
        "info": _serialize(info._mapping),
        "team_history": [_serialize(r._mapping) for r in team_history],
        "venues": rider_venue_history(session, canonical_id) if canonical_id is not None else [],
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
    roster = session.execute(
        text(f"""
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
    """),
        params,
    ).all()

    division_summary = session.execute(
        text(f"""
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
    """),
        params,
    ).all()

    event_performance = session.execute(
        text(f"""
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
    """),
        params,
    ).all()

    seasons_available = session.execute(
        text("""
        SELECT DISTINCT e.season
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.team = :team
        ORDER BY e.season
    """),
        {"team": team_name},
    ).all()

    return {
        "team_name": team_name,
        "roster": [_serialize(r._mapping) for r in roster],
        "division_summary": [_serialize(r._mapping) for r in division_summary],
        "event_performance": [_serialize(r._mapping) for r in event_performance],
        "seasons_available": [r[0] for r in seasons_available],
    }


# Sortable leaderboard columns: key -> (SQL expression, natural direction).
# Places are "lower is better", everything else "higher is better".
RIDER_SORTS: dict[str, tuple[str, str]] = {
    "avg_points": ("avg_points", "desc"),
    "total_points": ("total_points", "desc"),
    "avg_place": ("avg_place", "asc"),
    "best_place": ("best_place", "asc"),
    "races": ("races", "desc"),
    "name": ("c.name", "asc"),
    "division": ("r.division, r.gender", "asc"),
}
TEAM_SORTS: dict[str, tuple[str, str]] = {
    "avg_points": ("avg_points", "desc"),
    "total_points": ("total_points", "desc"),
    "avg_place": ("avg_place", "asc"),
    "best_place": ("best_place", "asc"),
    "races": ("races", "desc"),
    "riders": ("riders", "desc"),
    "team": ("ri.team", "asc"),
}


def leaderboard_order(
    sorts: dict[str, tuple[str, str]], metric: str, direction: str | None
) -> tuple[str, str, str]:
    """Resolve a requested sort to (metric, direction, ORDER BY sql).

    Unknown metrics fall back to avg_points; an unknown or missing direction
    uses the column's natural one. Only whitelisted expressions reach the SQL.
    """
    if metric not in sorts:
        metric = "avg_points"
    expr, natural = sorts[metric]
    direction = direction if direction in ("asc", "desc") else natural
    order = ", ".join(f"{col.strip()} {direction.upper()} NULLS LAST" for col in expr.split(","))
    return metric, direction, order


def leaderboard(
    session: Session,
    season: int | None = None,
    division: str | None = None,
    gender: str | None = None,
    metric: str = "avg_points",
    limit: int | None = 25,
    direction: str | None = None,
) -> list[dict]:
    """Top riders by chosen metric — merged riders unified.

    A rider needs two scored races to rank, except while the selected season
    has only one scored event (the opening weeks), when one race is enough —
    otherwise the season leaderboard is empty until race two.
    """
    params: dict = {}
    filters: list[str] = ["r.place IS NOT NULL", _POINTS_ONLY]
    scope_season = ""
    if season:
        filters.append("e.season = :season")
        scope_season = "AND e2.season = :season"
        params["season"] = season
    if division:
        filters.append("r.division = :division")
        params["division"] = division
    if gender:
        filters.append("r.gender = :gender")
        params["gender"] = gender

    where = " AND ".join(filters)
    _, _, order_col = leaderboard_order(RIDER_SORTS, metric, direction)
    limit_sql = "LIMIT :limit" if limit else ""

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
        HAVING count(DISTINCT r.event_id) >= LEAST(2, (
            SELECT count(*) FROM events e2
            WHERE e2.event_type = 'points' {scope_season}
        ))
        ORDER BY {order_col}
        {limit_sql}
    """
    if limit:
        params["limit"] = limit
    rows = session.execute(text(sql), params).all()
    return [_serialize(r._mapping) for r in rows]


def team_leaderboard(
    session: Session,
    season: int | None = None,
    limit: int | None = 25,
    metric: str = "avg_points",
    direction: str | None = None,
    min_riders: int = 1,
) -> list[dict]:
    """Teams ranked by their riders' results.

    `min_riders` hides tiny teams; the full leaderboard shows every team (a
    two-rider school squad still scored points), while the home page top-10
    keeps a floor so one strong rider can't top the league as a "team".
    """
    params: dict = {"min_riders": min_riders}
    if limit:
        params["limit"] = limit
    season_filter = ""
    if season:
        season_filter = "AND e.season = :season"
        params["season"] = season
    _, _, order_col = leaderboard_order(TEAM_SORTS, metric, direction)
    limit_sql = "LIMIT :limit" if limit else ""

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
        HAVING count(DISTINCT ri.id) >= :min_riders
        ORDER BY {order_col}
        {limit_sql}
    """
    rows = session.execute(text(sql), params).all()
    return [_serialize(r._mapping) for r in rows]


# ── Course queries ──────────────────────────────────────────────────


def courses_list(session: Session) -> list[dict]:
    rows = session.execute(
        text("""
        SELECT c.id, c.name, c.location, c.difficulty_score,
               ms.distance_miles AS ms_distance_miles, ms.elevation_ft AS ms_elevation_ft,
               hs.distance_miles AS hs_distance_miles, hs.elevation_ft AS hs_elevation_ft,
               count(DISTINCT e.id) AS event_count,
               count(r.id) AS result_count
        FROM courses c
        LEFT JOIN course_loops ms ON ms.course_id = c.id AND ms.loop_type = 'MS'
            AND ms.season IS NULL
        LEFT JOIN course_loops hs ON hs.course_id = c.id AND hs.loop_type = 'HS'
            AND hs.season IS NULL
        LEFT JOIN events e ON e.course_id = c.id
        LEFT JOIN results r ON r.event_id = e.id
        GROUP BY c.id, ms.distance_miles, ms.elevation_ft, hs.distance_miles, hs.elevation_ft
        ORDER BY c.name
    """)
    ).all()
    return [_serialize(r._mapping) for r in rows]


def course_detail(session: Session, course_id: int, season: int | None = None) -> dict | None:
    info = session.execute(
        text("""
        SELECT id, name, location, difficulty_score, notes
        FROM courses WHERE id = :id
    """),
        {"id": course_id},
    ).one_or_none()
    if not info:
        return None

    # Season rows win over the season-NULL defaults when a season is selected.
    loops = session.execute(
        text("""
        SELECT DISTINCT ON (loop_type) loop_type, distance_miles, elevation_ft
        FROM course_loops
        WHERE course_id = :id AND (season IS NULL OR season = :season)
        ORDER BY loop_type, season NULLS LAST
    """),
        {"id": course_id, "season": season},
    ).all()

    params: dict = {"cid": course_id}
    season_filter = ""
    if season:
        season_filter = "AND e.season = :season"
        params["season"] = season

    events_at = session.execute(
        text(f"""
        SELECT e.id, e.season, e.event_order, e.event_name,
               count(r.id) AS results
        FROM events e
        LEFT JOIN results r ON r.event_id = e.id
        WHERE e.course_id = :cid {season_filter}
        GROUP BY e.id
        ORDER BY e.season, e.event_order
    """),
        params,
    ).all()

    laps = session.execute(
        text("""
        SELECT division, gender, lap_count, max_duration_mins, cutoff_mins
        FROM (
            SELECT DISTINCT ON (division, gender)
                   division, gender, lap_count, max_duration_mins, cutoff_mins
            FROM division_laps
            WHERE course_id = :cid AND (season IS NULL OR season = :season)
            ORDER BY division, gender, season NULLS LAST
        ) resolved
        ORDER BY lap_count DESC, division, gender
    """),
        {"cid": course_id, "season": season},
    ).all()

    division_stats = session.execute(
        text(f"""
        SELECT r.division, r.gender,
               min(dl.loop_type) AS loop_type,
               -- profiles are per season, so the all-time view may span
               -- several lap counts / loop lengths: list them rather than
               -- splitting the division into one row per profile
               string_agg(DISTINCT dl.lap_count::text, '/' ORDER BY dl.lap_count::text)
                   AS lap_count,
               string_agg(DISTINCT cl.distance_miles::text, '/' ORDER BY cl.distance_miles::text)
                   AS loop_distance,
               count(DISTINCT COALESCE(ra.canonical_id, ri.id)) AS riders,
               count(r.id) AS results,
               round(avg(r.place)::numeric, 1) AS avg_place,
               round(avg(r.points)::numeric, 1) AS avg_points,
               round(avg(EXTRACT(EPOCH FROM r.total_time))
                     FILTER (WHERE {_FULL_DISTANCE})::numeric, 1) AS avg_time_secs,
               min(r.total_time) FILTER (WHERE {_FULL_DISTANCE}) AS fastest_time,
               round(avg(
                   {_RIDE_SECS} / NULLIF({_ACTUAL_LAPS}, 0)
               ) FILTER (WHERE {_LAPS_CONSISTENT})::numeric, 1) AS avg_pace_per_lap_secs,
               round(avg(
                   ({_RIDE_SECS} / 60.0)
                   / NULLIF({_ACTUAL_LAPS} * cl.distance_miles, 0)
               ) FILTER (WHERE {_LAPS_CONSISTENT} AND cl.distance_miles > 0)::numeric, 1) AS avg_min_per_mile
        FROM {_COURSE_RESULTS}
        JOIN events e ON r.event_id = e.id
        JOIN riders ri ON r.rider_id = ri.id
        LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
        {_LAP_JOINS}
        WHERE r.place IS NOT NULL AND r.total_time IS NOT NULL
          AND r.total_time < interval '2 hours'
          {season_filter}
        GROUP BY r.division, r.gender
        ORDER BY r.division, r.gender
    """),
        params,
    ).all()

    top_riders = session.execute(
        text(f"""
        WITH {_CANONICAL_CTE}
        SELECT
            c.cid AS rider_id,
            c.name,
            string_agg(DISTINCT c.team, ' / ' ORDER BY c.team) AS team,
            r.division,
            count(DISTINCT e.id) AS appearances,
            round(avg(r.place)::numeric, 1) AS avg_place,
            round(avg(r.points)::numeric, 1) AS avg_points,
            min(r.total_time) FILTER (WHERE {_FULL_DISTANCE}) AS best_time
        FROM {_COURSE_RESULTS}
        JOIN events e ON r.event_id = e.id
        JOIN canonical c ON c.rider_id = r.rider_id
        WHERE r.place IS NOT NULL
          {season_filter}
        GROUP BY c.cid, c.name, r.division
        HAVING count(DISTINCT e.id) >= 2
        ORDER BY avg_points DESC NULLS LAST
        LIMIT 20
    """),
        params,
    ).all()

    seasons_available = session.execute(
        text("""
        SELECT DISTINCT e.season
        FROM events e WHERE e.course_id = :cid
        ORDER BY e.season
    """),
        {"cid": course_id},
    ).all()

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
    canonical_id = session.execute(
        text("""
        SELECT COALESCE(
            (SELECT canonical_id FROM rider_aliases WHERE rider_id = :id),
            :id
        )
    """),
        {"id": rider_id},
    ).scalar()

    group_ids = session.execute(
        text("""
        SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
        UNION SELECT :cid
    """),
        {"cid": canonical_id},
    ).all()
    all_ids = [r[0] for r in group_ids]

    info = session.execute(
        text("""
        SELECT ri.id, ri.name,
               string_agg(DISTINCT ri2.team, ' / ' ORDER BY ri2.team) AS team
        FROM riders ri
        CROSS JOIN riders ri2
        WHERE ri.id = :cid AND ri2.id = ANY(:ids)
        GROUP BY ri.id, ri.name
    """),
        {"cid": canonical_id, "ids": all_ids},
    ).one_or_none()
    if not info:
        return None

    races = session.execute(
        text(f"""
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
                     ({_RIDE_SECS} / 60.0)
                     / ({_ACTUAL_LAPS} * cl.distance_miles)
                 )::numeric, 1)
            END AS min_per_mile,
            CASE WHEN cl.distance_miles > 0 AND cl.elevation_ft IS NOT NULL
                 THEN round((cl.elevation_ft / cl.distance_miles)::numeric, 1)
            END AS elevation_ft_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        {_LAP_JOINS}
        WHERE r.rider_id = ANY(:ids)
          AND r.place IS NOT NULL
          AND r.status = 'OK'
        ORDER BY e.season, e.event_order
    """),
        {"ids": all_ids},
    ).all()

    # Determine primary division and gender (most recent). Apply the pace
    # sanity range so rally/short-track events with bad loop distances don't
    # contaminate the rider's baseline.
    valid_races = [
        r
        for r in races
        if r.min_per_mile is not None and _PACE_MIN <= float(r.min_per_mile) <= _PACE_MAX
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
    canonical_id = session.execute(
        text("""
        SELECT COALESCE(
            (SELECT canonical_id FROM rider_aliases WHERE rider_id = :id),
            :id
        )
    """),
        {"id": rider_id},
    ).scalar()

    group_ids = session.execute(
        text("""
        SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid
        UNION SELECT :cid
    """),
        {"cid": canonical_id},
    ).all()
    all_ids = [r[0] for r in group_ids]

    rows = session.execute(
        text(f"""
        WITH base AS (
            SELECT
                e.id AS event_id, e.season, e.event_order, e.event_name,
                dl.loop_type AS age_group, r.gender, r.division,
                COALESCE(ra.canonical_id, ri.id) AS canonical_id,
                CASE WHEN r.total_time IS NOT NULL
                          AND r.total_time < interval '2 hours'
                          AND {_LAPS_CONSISTENT}
                     THEN {_RIDE_SECS} / NULLIF({_ACTUAL_LAPS}, 0)
                END AS lap_secs,
                CASE WHEN r.total_time IS NOT NULL
                          AND r.total_time < interval '2 hours'
                          AND cl.distance_miles > 0
                          AND {_LAPS_CONSISTENT}
                     THEN ({_RIDE_SECS} / 60.0)
                          / ({_ACTUAL_LAPS} * cl.distance_miles)
                END AS min_per_mile
            FROM results r
            JOIN events e ON r.event_id = e.id AND e.event_type = 'points'
            JOIN riders ri ON r.rider_id = ri.id
            LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
            {_LAP_JOINS_INNER}
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
    """),
        {"ids": all_ids, "cid": canonical_id, "minf": min_field},
    ).all()

    return [_serialize(r._mapping) for r in rows]


def staging_rows(
    session: Session, age_group: str, gender: str, season: int, min_field: int = 8
) -> list[dict]:
    """Per-(rider, event) z-scores for a whole category (age_group + gender) in
    one season — the basis for the staging grid. Field = every rider in that
    event + category; partition is per event since the category is fixed.
    """
    rows = session.execute(
        text(f"""
        WITH base AS (
            SELECT
                e.id AS event_id, e.event_name, e.event_order,
                COALESCE(ra.canonical_id, ri.id) AS canonical_id,
                cri.name AS name, cri.team AS team,
                r.division,
                tc.conference, tc.conference_group,
                CASE WHEN r.total_time IS NOT NULL
                          AND r.total_time < interval '2 hours'
                          AND {_LAPS_CONSISTENT}
                     THEN {_RIDE_SECS} / NULLIF({_ACTUAL_LAPS}, 0)
                END AS lap_secs,
                CASE WHEN r.total_time IS NOT NULL
                          AND r.total_time < interval '2 hours'
                          AND cl.distance_miles > 0
                          AND {_LAPS_CONSISTENT}
                     THEN ({_RIDE_SECS} / 60.0)
                          / ({_ACTUAL_LAPS} * cl.distance_miles)
                END AS min_per_mile
            FROM results r
            JOIN events e ON r.event_id = e.id AND e.event_type = 'points'
                AND e.season = :season
            JOIN riders ri ON r.rider_id = ri.id
            LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
            JOIN riders cri ON cri.id = COALESCE(ra.canonical_id, ri.id)
            LEFT JOIN team_conferences tc ON tc.team = ri.team AND tc.season = e.season
            {_LAP_JOINS_AGE_GROUP}
            WHERE r.status = 'OK' AND r.place IS NOT NULL AND r.gender = :gender
        ),
        clean AS (
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
            WINDOW w AS (PARTITION BY event_id)
        )
        SELECT canonical_id, name, team, division, conference, conference_group,
               event_id, event_name, event_order,
               CASE WHEN lap_std > 0 AND lap_field >= :minf
                    THEN round(((lap_secs - lap_mean) / lap_std)::numeric, 2) END AS z_lap,
               CASE WHEN pace_std > 0 AND pace_field >= :minf
                    THEN round(((pace_ok - pace_mean) / pace_std)::numeric, 2) END AS z_pace
        FROM z
        ORDER BY event_order, name
    """),
        {"season": season, "age_group": age_group, "gender": gender, "minf": min_field},
    ).all()

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

    rows = session.execute(
        text(f"""
        SELECT
            r.place,
            count(*) OVER (PARTITION BY r.event_id) AS field_size,
            round((
                ({_RIDE_SECS} / 60.0)
                / NULLIF({_ACTUAL_LAPS} * cl.distance_miles, 0)
            )::numeric, 1) AS min_per_mile
        FROM results r
        JOIN events e ON r.event_id = e.id
        {_LAP_JOINS}
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
    """),
        params,
    ).all()

    paces = [
        float(r[2]) for r in rows if r[2] is not None and _PACE_MIN <= float(r[2]) <= _PACE_MAX
    ]
    field_sizes = list({r[1] for r in rows if r[1]})

    return {"paces": paces, "field_sizes": field_sizes}


def division_profile_lookup(
    session: Session,
    division: str,
    gender: str,
    course_id: int | None = None,
    season: int | None = None,
) -> dict | None:
    """Lap count, loop type, loop distance and climbing for a division.

    With a course, that course's profile is used — its row for `season` when
    one exists, else the course default. Without a course the league-wide
    defaults apply (every course seeds the same spreadsheet values), which is
    all the forecast can do before it knows where the race is.
    """
    div_filter = "dl.division = :division"
    params: dict = {"division": division, "gender": gender, "season": season}
    if division in ("MS Advanced", "Middle School Advanced"):
        div_filter = "dl.division IN ('MS Advanced', 'Middle School Advanced')"
    if course_id is not None:
        scope = "AND dl.course_id = :course_id AND (dl.season = :season OR dl.season IS NULL)"
        params["course_id"] = course_id
    else:
        scope = "AND dl.season IS NULL"

    row = session.execute(
        text(f"""
        SELECT dl.lap_count, dl.loop_type, cl.distance_miles, cl.elevation_ft, dl.season
        FROM division_laps dl
        JOIN LATERAL (
            SELECT l.distance_miles, l.elevation_ft
            FROM course_loops l
            WHERE l.course_id = dl.course_id AND l.loop_type = dl.loop_type
              AND (l.season = :season OR l.season IS NULL)
            ORDER BY l.season NULLS LAST
            LIMIT 1
        ) cl ON true
        WHERE {div_filter}
          AND (dl.gender = :gender OR (dl.gender IS NULL AND :gender IS NULL))
          AND dl.loop_type IS NOT NULL
          {scope}
        ORDER BY dl.season NULLS LAST, dl.course_id
        LIMIT 1
    """),
        params,
    ).one_or_none()

    if not row:
        return None
    lap_count, loop_type, miles, elev, profile_season = row
    climb = round(elev / miles, 1) if miles and elev is not None else None
    return {
        "lap_count": lap_count,
        "loop_type": loop_type,
        "loop_miles": float(miles),
        "elevation_ft_per_mile": climb,
        "profile_season": profile_season,
    }


def forecast_courses(session: Session) -> list[dict]:
    """Courses that have hosted a points race — the forecast page's course picker."""
    rows = session.execute(
        text("""
        SELECT c.id, c.name, max(e.season) AS last_season
        FROM courses c
        JOIN events e ON e.course_id = c.id AND e.event_type = 'points'
        GROUP BY c.id, c.name
        ORDER BY c.name
    """)
    ).all()
    return [_serialize(r._mapping) for r in rows]


def available_target_divisions(session: Session, source_division: str, gender: str) -> list[str]:
    """Get divisions a rider could be forecast into (same gender, exclude source and single-lap)."""
    rows = session.execute(
        text("""
        SELECT DISTINCT r.division
        FROM results r
        WHERE r.gender = :gender
          AND r.division != :source
          AND r.division NOT LIKE 'Single Lap%%'
          AND r.division != '9th Grade'
          AND r.place IS NOT NULL
        ORDER BY r.division
    """),
        {"gender": gender, "source": source_division},
    ).all()
    return [r[0] for r in rows]


# ── Race-position bump chart (the /racechart page) ──────────────────────


def events_list(session: Session) -> list[dict]:
    """Events that have any lap timing, newest first — the chart's event picker."""
    rows = session.execute(
        text("""
        SELECT e.id, e.season, e.event_name, e.event_order
        FROM events e
        WHERE EXISTS (
            SELECT 1 FROM results r
            WHERE r.event_id = e.id AND r.lap1 IS NOT NULL
        )
        ORDER BY e.season DESC, e.event_order DESC
    """)
    ).all()
    return [dict(r._mapping) for r in rows]


def event_categories(session: Session, event_id: int) -> list[dict]:
    """Categories in one event that have lap timing, in race order."""
    rows = session.execute(
        text("""
        SELECT category, max(category_order) AS category_order, count(*) AS field
        FROM results
        WHERE event_id = :eid AND lap1 IS NOT NULL
        GROUP BY category
        ORDER BY category_order
    """),
        {"eid": event_id},
    ).all()
    return [dict(r._mapping) for r in rows]


def event_lap_rows(session: Session, event_id: int, category: str) -> list[dict]:
    """Per-rider lap splits (in seconds) for one event + category.

    Laps are returned as float seconds rather than the Interval default so the
    pure racechart module can sum them without timedelta handling. Ordered by
    official place so unranked riders (DNF/DSQ) sort last.
    """
    rows = session.execute(
        text("""
        SELECT r.bib, ri.name AS name, ri.team AS team, r.status, r.place,
               EXTRACT(EPOCH FROM r.lap1) AS lap1,
               EXTRACT(EPOCH FROM r.lap2) AS lap2,
               EXTRACT(EPOCH FROM r.lap3) AS lap3,
               EXTRACT(EPOCH FROM r.lap4) AS lap4,
               EXTRACT(EPOCH FROM r.lap5) AS lap5,
               EXTRACT(EPOCH FROM r.lap6) AS lap6
        FROM results r
        JOIN riders ri ON ri.id = r.rider_id
        WHERE r.event_id = :eid AND r.category = :cat
        ORDER BY r.place NULLS LAST, r.bib
    """),
        {"eid": event_id, "cat": category},
    ).all()
    return [dict(r._mapping) for r in rows]


# ── Race results (the /results page) ────────────────────────────────────


def all_events(session: Session) -> list[dict]:
    """Every scraped event, newest first, with its course and result count."""
    rows = session.execute(
        text("""
        SELECT e.id, e.season, e.event_order, e.event_name, e.event_type, e.raceresult_id,
               c.name AS course_name,
               count(r.id) AS result_count,
               bool_or(r.lap1 IS NOT NULL) AS has_laps
        FROM events e
        LEFT JOIN courses c ON c.id = e.course_id
        LEFT JOIN results r ON r.event_id = e.id
        GROUP BY e.id, c.name
        ORDER BY e.season DESC, e.event_order DESC
    """)
    ).all()
    return [dict(r._mapping) for r in rows]


def event_result_categories(session: Session, event_id: int) -> list[dict]:
    """Every category raced in one event (not just those with lap timing), in race order."""
    rows = session.execute(
        text("""
        SELECT category, max(category_order) AS category_order,
               count(*) AS field, bool_or(lap1 IS NOT NULL) AS has_laps
        FROM results
        WHERE event_id = :eid
        GROUP BY category
        ORDER BY category_order, category
    """),
        {"eid": event_id},
    ).all()
    return [dict(r._mapping) for r in rows]


def event_results(session: Session, event_id: int, category: str) -> list[dict]:
    """The finish list for one event + category, as published.

    Ordered by official place with DNF/DSQ last. `gap_secs` is the time behind
    the winner for riders who covered the full distance; riders pulled early
    get `laps_down` instead. Rider links resolve to the canonical (merged) id.
    """
    rows = session.execute(
        text(f"""
        WITH cat AS (
            SELECT r.*,
                   {_ACTUAL_LAPS} AS laps,
                   max({_ACTUAL_LAPS}) OVER () AS full_laps,
                   first_value(r.total_time) OVER (ORDER BY r.place NULLS LAST) AS win_time
            FROM results r
            WHERE r.event_id = :eid AND r.category = :cat
        )
        SELECT c.place, c.status, c.bib, c.points, c.conference, c.laps, c.full_laps,
               c.total_time_raw,
               CASE WHEN c.place IS NOT NULL AND c.laps = c.full_laps AND c.total_time IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (c.total_time - c.win_time)) END AS gap_secs,
               CASE WHEN c.place IS NOT NULL AND c.laps < c.full_laps
                    THEN c.full_laps - c.laps END AS laps_down,
               EXTRACT(EPOCH FROM c.lap1) AS lap1, EXTRACT(EPOCH FROM c.lap2) AS lap2,
               EXTRACT(EPOCH FROM c.lap3) AS lap3, EXTRACT(EPOCH FROM c.lap4) AS lap4,
               EXTRACT(EPOCH FROM c.lap5) AS lap5, EXTRACT(EPOCH FROM c.lap6) AS lap6,
               ri.name, ri.team, COALESCE(ra.canonical_id, ri.id) AS rider_id
        FROM cat c
        JOIN riders ri ON ri.id = c.rider_id
        LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
        ORDER BY c.place NULLS LAST, c.status, c.bib
    """),
        {"eid": event_id, "cat": category},
    ).all()
    return [_serialize(r._mapping) for r in rows]


# ── Venue history (rider page "By Venue", team page "Course History") ────

_VENUE_ROW_SQL = f"""
    SELECT
        e.id AS event_id, e.season, e.event_order, e.event_name, e.event_type,
        e.course_id, co.name AS course_name,
        r.rider_id, ri.name AS rider_name, COALESCE(ra.canonical_id, ri.id) AS canonical_id,
        r.category, r.division, r.gender, r.place, r.points, r.status, r.total_time_raw,
        {_ACTUAL_LAPS} AS laps,
        (SELECT count(*) FROM results x
          WHERE x.event_id = r.event_id AND x.category = r.category AND x.place IS NOT NULL)
            AS field,
        CASE WHEN r.total_time IS NOT NULL
                  AND r.total_time < interval '2 hours'
                  AND cl.distance_miles > 0
                  AND {_LAPS_CONSISTENT}
             THEN round((
                 ({_RIDE_SECS} / 60.0)
                 / ({_ACTUAL_LAPS} * cl.distance_miles)
             )::numeric, 2)
        END AS min_per_mile
    FROM results r
    JOIN events e ON r.event_id = e.id
    JOIN courses co ON co.id = e.course_id
    JOIN riders ri ON ri.id = r.rider_id
    LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
    {_LAP_JOINS}
"""


def _with_speed(row: dict) -> dict:
    """Add mph (from min/mile), blank out implausible pace, and add the field
    percentile — same formula as the rider page's Field Percentile chart
    (100 × (1 − place/field)), so 24th of 85 is 71.8 and 28th of 66 is 57.6:
    comparable across years even as the field grows."""
    mpm = row.get("min_per_mile")
    if mpm is not None and not (_PACE_MIN <= float(mpm) <= _PACE_MAX):
        mpm = None
        row["min_per_mile"] = None
    row["mph"] = round(60.0 / float(mpm), 1) if mpm else None
    place, field = row.get("place"), row.get("field")
    row["percentile"] = round((1 - place / field) * 100, 1) if place and field else None
    return row


def rider_venue_history(session: Session, rider_id: int) -> list[dict]:
    """Every visit a rider (canonical group) made to each course, oldest first,
    with the change in pace and place versus their previous visit there.

    Returns [{course_id, course_name, visits: [...]}], courses with the most
    visits first so the year-over-year comparisons lead.
    """
    canonical_id = session.execute(
        text("""
        SELECT COALESCE((SELECT canonical_id FROM rider_aliases WHERE rider_id = :id), :id)
    """),
        {"id": rider_id},
    ).scalar()
    ids = [
        r[0]
        for r in session.execute(
            text("SELECT rider_id FROM rider_aliases WHERE canonical_id = :cid UNION SELECT :cid"),
            {"cid": canonical_id},
        ).all()
    ]
    rows = session.execute(
        text(
            _VENUE_ROW_SQL
            + """
    WHERE r.rider_id = ANY(:ids)
    ORDER BY co.name, e.season, e.event_order
    """
        ),
        {"ids": ids},
    ).all()

    by_course: dict[int, dict] = {}
    for raw in rows:
        row = _with_speed(_serialize(raw._mapping))
        block = by_course.setdefault(
            row["course_id"],
            {"course_id": row["course_id"], "course_name": row["course_name"], "visits": []},
        )
        prev = next((v for v in reversed(block["visits"]) if v["event_type"] == "points"), None)
        row["d_pace"] = row["d_percentile"] = None
        if prev and row["event_type"] == "points":
            if row["min_per_mile"] is not None and prev["min_per_mile"] is not None:
                row["d_pace"] = round(float(row["min_per_mile"]) - float(prev["min_per_mile"]), 2)
            if row["percentile"] is not None and prev["percentile"] is not None:
                row["d_percentile"] = round(row["percentile"] - prev["percentile"], 1)
        block["visits"].append(row)
    return sorted(by_course.values(), key=lambda b: (-len(b["visits"]), b["course_name"]))


def team_courses(session: Session, team_name: str) -> list[dict]:
    """Courses this team has raced at, most-visited first."""
    rows = session.execute(
        text("""
        SELECT co.id, co.name, count(DISTINCT e.season) AS seasons, count(r.id) AS results
        FROM results r
        JOIN riders ri ON ri.id = r.rider_id
        JOIN events e ON e.id = r.event_id
        JOIN courses co ON co.id = e.course_id
        WHERE ri.team = :team
        GROUP BY co.id, co.name
        ORDER BY seasons DESC, results DESC, co.name
    """),
        {"team": team_name},
    ).all()
    return [dict(r._mapping) for r in rows]


def team_course_history(session: Session, team_name: str, course_id: int) -> dict:
    """Rider × season grid for one team at one course.

    Riders are everyone who rode for the team at that course in any season
    (via their canonical id, so a rename doesn't split them). Cells hold the
    rider's result there that season; the newest riders come first.
    """
    rows = session.execute(
        text(
            _VENUE_ROW_SQL
            + """
    WHERE e.course_id = :cid
      AND COALESCE(ra.canonical_id, ri.id) IN (
          SELECT COALESCE(a.canonical_id, x.id)
          FROM riders x LEFT JOIN rider_aliases a ON a.rider_id = x.id
          WHERE x.team = :team
      )
    ORDER BY e.season, e.event_order
    """
        ),
        {"cid": course_id, "team": team_name},
    ).all()
    seasons: list[int] = sorted({r.season for r in rows})
    riders: dict[int, dict] = {}
    for raw in rows:
        row = _with_speed(_serialize(raw._mapping))
        rider = riders.setdefault(
            row["canonical_id"], {"id": row["canonical_id"], "name": row["rider_name"], "cells": {}}
        )
        # Two events at one course in a season (Belmont, Blue Mountain): keep the later one.
        rider["cells"][row["season"]] = row
    ordered = sorted(riders.values(), key=lambda x: (-max(x["cells"]), -len(x["cells"]), x["name"]))
    return {"seasons": seasons, "riders": ordered}
