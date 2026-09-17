"""Seed reference data: conferences, courses, division lap profiles."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.db.engine import rowcount

logger = logging.getLogger(__name__)

# ── Courses ──────────────────────────────────────────────────────────
# Venue name → event name patterns that map to this course
COURSES = {
    "Granite": {
        "location": "Granite, PA",
        "patterns": ["Granite"],
    },
    "Johnstown": {
        "location": "Johnstown, PA",
        "patterns": ["Johnstown"],
    },
    "Boyce": {
        "location": "Boyce Park, PA",
        "patterns": ["Boyce"],
    },
    "Blue Mountain": {
        "location": "Blue Mountain, PA",
        "patterns": ["Blue Mtn", "Blue Mountain"],
    },
    "Fair Hill": {
        "location": "Fair Hill, MD",
        "patterns": ["Fair Hill", "FairHill"],
    },
    "Penn College": {
        "location": "Williamsport, PA",
        "patterns": ["Penn College"],
    },
    "Oesterling": {
        "location": "Oesterling, PA",
        "patterns": ["Osterling", "Oesterling"],
    },
    "Coleman": {
        "location": "Coleman, PA",
        "patterns": ["Coleman"],
    },
    "Wainer": {
        "location": "Wainer, PA",
        "patterns": ["Wainer"],
    },
    "Belmont": {
        "location": "Belmont, PA",
        "patterns": ["Belmont"],
    },
    "Alameda": {
        "location": "Alameda, PA",
        "patterns": ["Alameda"],
    },
    "Harvest Fields": {
        "location": "Harvest Fields, PA",
        "patterns": ["Harvest Fields"],
    },
    "Hershey": {
        "location": "Hershey, PA",
        "patterns": ["Hershey"],
    },
    "Birdsboro": {  # new venue on the 2026 calendar (Rustic Park @ Birdsboro Preserve)
        "location": "Birdsboro, PA",
        "patterns": ["Birdsboro"],
    },
}

# ── Division lap profiles (from PICL spreadsheet) ────────────────────
# (division, gender, laps, max_duration_mins, cutoff_mins, loop_type)
# MS divisions ride the MS loop (~1 mi); HS divisions ride the HS loop (~1.5 mi)
# MS Advanced rides the MS loop but does more laps
DIVISION_PROFILES = [
    ("Varsity", "Male", 4, 90, 68, "HS"),
    ("Varsity", "Female", 4, 90, 68, "HS"),
    ("JV1", "Male", 3, 75, 50, "HS"),
    ("JV1", "Female", 3, 75, 50, "HS"),
    ("JV2", "Male", 2, 75, 38, "HS"),
    ("JV2", "Female", 2, 75, 38, "HS"),
    ("JV3", "Male", 2, 75, 38, "HS"),
    ("JV3", "Female", 2, 75, 38, "HS"),
    ("Middle School Advanced", "Male", 4, 60, 45, "MS"),
    ("Middle School Advanced", "Female", 4, 60, 45, "MS"),
    ("MS Advanced", "Male", 4, 60, 45, "MS"),
    ("MS Advanced", "Female", 4, 60, 45, "MS"),
    ("8th Grade", "Male", 2, 45, 23, "MS"),
    ("8th Grade", "Female", 2, 45, 23, "MS"),
    ("7th Grade", "Male", 2, 45, 23, "MS"),
    ("7th Grade", "Female", 2, 45, 23, "MS"),
    ("6th Grade", "Male", 2, 45, 23, "MS"),
    ("6th Grade", "Female", 2, 45, 23, "MS"),
    ("5th Grade", "Male", 2, 45, 23, "MS"),
    ("5th Grade", "Female", 2, 45, 23, "MS"),
    ("Single Lap High School", None, 1, None, None, "HS"),
    ("Single Lap Middle School", None, 1, None, None, "MS"),
]

# (division, gender) pairs the admin course form edits, in display order. The
# "Middle School Advanced" alias is folded into "MS Advanced" in results (see
# DIVISION_ALIASES), so it is not offered as a separate row.
PROFILE_KEYS: list[tuple[str, str | None]] = [
    (div, gender) for div, gender, *_ in DIVISION_PROFILES if div != "Middle School Advanced"
]

# Default loop distances. Seeded only when a course has no loop row yet;
# values entered in /admin/courses are never overwritten by a re-seed.
DEFAULT_LOOP_DISTANCES = {
    "MS": 2.0,  # ~2 miles (based on Granite, Hershey, Penn College actuals)
    "HS": 3.5,  # ~3.5 miles (based on Penn College, Hershey actuals)
}

# ── Conference lineage ──────────────────────────────────────────────
# Maps 2025 conferences back to their historical grouping
CONFERENCE_LINEAGE = {
    "Eastern": "Eastern",
    "Eastern Blue": "Eastern",
    "Eastern  Blue": "Eastern",  # double-space variant in data
    "Eastern Gold": "Eastern",
    "Central": "Central",
    "Western": "Western",
}


def seed_courses(session: Session) -> dict[str, int]:
    """Insert courses and return name→id mapping."""
    course_ids: dict[str, int] = {}
    for name, info in COURSES.items():
        session.execute(
            text(
                "INSERT INTO courses (name, location) VALUES (:name, :loc) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "loc": info.get("location")},
        )

    # Fetch IDs
    rows = session.execute(text("SELECT id, name FROM courses")).all()
    course_ids = {r[1]: r[0] for r in rows}
    logger.info("Seeded %d courses", len(course_ids))
    return course_ids


def map_events_to_courses(session: Session, course_ids: dict[str, int]) -> int:
    """Map events to courses based on event name patterns."""
    count = 0
    events = session.execute(
        text("SELECT id, event_name FROM events WHERE course_id IS NULL")
    ).all()

    for event_id, event_name in events:
        for course_name, info in COURSES.items():
            if any(p.lower() in event_name.lower() for p in info["patterns"]):
                session.execute(
                    text("UPDATE events SET course_id = :cid WHERE id = :eid"),
                    {"cid": course_ids[course_name], "eid": event_id},
                )
                count += 1
                break

    logger.info("Mapped %d events to courses", count)
    return count


def seed_course_loops(session: Session, course_ids: dict[str, int]) -> int:
    """Seed MS and HS loops for every course with default distances."""
    count = 0
    for course_name, course_id in course_ids.items():
        for loop_type, distance in DEFAULT_LOOP_DISTANCES.items():
            session.execute(
                text("""
                INSERT INTO course_loops (course_id, loop_type, distance_miles, season)
                VALUES (:cid, :lt, :dist, NULL)
                ON CONFLICT (course_id, loop_type, season) DO NOTHING
            """),
                {"cid": course_id, "lt": loop_type, "dist": distance},
            )
            count += 1
    logger.info("Seeded %d course loops", count)
    return count


def seed_division_laps(session: Session, course_ids: dict[str, int]) -> int:
    """Seed division lap profiles for all courses.

    Default profiles have season = NULL (and the single-lap ones gender = NULL).
    Postgres treats NULLs as distinct in the unique constraint, so an
    ON CONFLICT upsert never matches them and every seed run would insert a
    second copy — which then double-counts each race wherever results join
    division_laps. Update-then-insert with IS NOT DISTINCT FROM is idempotent.
    """
    count = 0
    for course_name, course_id in course_ids.items():
        for div, gender, laps, max_dur, cutoff, loop_type in DIVISION_PROFILES:
            params = {
                "cid": course_id,
                "div": div,
                "gender": gender,
                "laps": laps,
                "max_dur": max_dur,
                "cutoff": cutoff,
                "lt": loop_type,
            }
            updated = session.execute(
                text("""
                UPDATE division_laps
                SET lap_count = :laps, max_duration_mins = :max_dur,
                    cutoff_mins = :cutoff, loop_type = :lt
                WHERE course_id = :cid AND division = :div
                  AND gender IS NOT DISTINCT FROM :gender
                  AND season IS NULL
            """),
                params,
            )
            if rowcount(updated) == 0:
                session.execute(
                    text("""
                    INSERT INTO division_laps (course_id, division, gender, lap_count,
                        max_duration_mins, cutoff_mins, loop_type)
                    VALUES (:cid, :div, :gender, :laps, :max_dur, :cutoff, :lt)
                """),
                    params,
                )
            count += 1

    logger.info("Seeded %d division-lap profiles", count)
    return count


# Laps a result actually recorded: split columns that are populated. Mirrors
# web/queries._ACTUAL_LAPS; kept separate so the db layer doesn't import web.
RIDDEN_LAPS_SQL = """
    ((r.lap1 IS NOT NULL)::int + (r.lap2 IS NOT NULL)::int + (r.lap3 IS NOT NULL)::int
   + (r.lap4 IS NOT NULL)::int + (r.lap5 IS NOT NULL)::int + (r.lap6 IS NOT NULL)::int)
"""

# Fewer consistent finishers than this and the recorded-lap mode is not
# trusted; the season row copies the default lap count instead.
MIN_FINISHERS_FOR_MODE = 3


def seed_season_profiles(session: Session) -> tuple[int, int]:
    """Create per-season loop and lap rows for every course-season with results.

    Loop rows copy the course default distance/elevation. Lap counts come from
    the data: the most common number of recorded laps among OK finishers whose
    splits add up to their total time. Rows are only ever inserted — anything
    already present (seeded earlier or entered in /admin/courses) is kept, so
    re-running seed after a new race never undoes an admin's edits.

    Returns (loop rows inserted, lap rows inserted).
    """
    course_seasons = session.execute(
        text("""
        SELECT DISTINCT e.course_id, e.season
        FROM events e
        WHERE e.course_id IS NOT NULL AND e.event_type = 'points' AND e.season > 0
        ORDER BY e.course_id, e.season
    """)
    ).all()

    loops_added = 0
    for course_id, season in course_seasons:
        result = session.execute(
            text("""
            INSERT INTO course_loops (course_id, loop_type, distance_miles, elevation_ft, season)
            SELECT course_id, loop_type, distance_miles, elevation_ft, :season
            FROM course_loops
            WHERE course_id = :cid AND season IS NULL
            ON CONFLICT (course_id, loop_type, season) DO NOTHING
        """),
            {"cid": course_id, "season": season},
        )
        loops_added += rowcount(result)

    laps_added = 0
    for course_id, season in course_seasons:
        result = session.execute(
            text(f"""
            WITH ridden AS (
                SELECT r.division, r.gender,
                       mode() WITHIN GROUP (ORDER BY {RIDDEN_LAPS_SQL}) AS laps,
                       count(*) AS finishers
                FROM results r
                JOIN events e ON e.id = r.event_id
                WHERE e.course_id = :cid AND e.season = :season AND e.event_type = 'points'
                  AND r.status = 'OK' AND r.total_time IS NOT NULL
                  AND {RIDDEN_LAPS_SQL} > 0
                  AND abs(EXTRACT(EPOCH FROM (r.total_time - COALESCE(r.penalty, interval '0') - (
                        COALESCE(r.lap1, interval '0') + COALESCE(r.lap2, interval '0')
                      + COALESCE(r.lap3, interval '0') + COALESCE(r.lap4, interval '0')
                      + COALESCE(r.lap5, interval '0') + COALESCE(r.lap6, interval '0'))))) < 10
                GROUP BY r.division, r.gender
            )
            INSERT INTO division_laps (course_id, division, gender, lap_count,
                max_duration_mins, cutoff_mins, loop_type, season)
            SELECT d.course_id, d.division, d.gender,
                   CASE WHEN x.finishers >= :min_n THEN x.laps ELSE d.lap_count END,
                   d.max_duration_mins, d.cutoff_mins, d.loop_type, :season
            FROM division_laps d
            JOIN ridden x ON x.division = d.division AND x.gender IS NOT DISTINCT FROM d.gender
            WHERE d.course_id = :cid AND d.season IS NULL
            ON CONFLICT (course_id, division, gender, season) DO NOTHING
        """),
            {"cid": course_id, "season": season, "min_n": MIN_FINISHERS_FOR_MODE},
        )
        laps_added += rowcount(result)

    logger.info(
        "Season profiles: %d course-seasons, %d loop rows and %d lap rows added",
        len(course_seasons),
        loops_added,
        laps_added,
    )
    return loops_added, laps_added


# Division label aliases → canonical name. Same division recorded under
# different strings across seasons; fold them so leaderboards/profiles don't
# split one rider into two division rows.
DIVISION_ALIASES = {
    "Middle School Advanced": "MS Advanced",
}


def normalize_divisions(session: Session) -> int:
    """Fold aliased division labels to their canonical name in results.

    Idempotent. division_laps keeps rows for both labels, so pace joins still
    resolve after the rename.
    """
    total = 0
    for alias, canonical in DIVISION_ALIASES.items():
        result = session.execute(
            text("UPDATE results SET division = :canon WHERE division = :alias"),
            {"canon": canonical, "alias": alias},
        )
        total += rowcount(result)
    logger.info("Normalized %d result rows to canonical division labels", total)
    return total


def classify_event_types(session: Session) -> int:
    """Classify events as 'rally'/'exhibition' (non-scoring) by name pattern.

    Rallies and exhibition/short-track events do not count toward standings.
    Idempotent: re-derives event_type for every event from its name, so newly
    scraped events get classified on the next seed (like course mapping).
    """
    result = session.execute(
        text("""
        UPDATE events SET event_type =
            CASE
                WHEN event_name ILIKE '%exhibition%'
                  OR event_name ILIKE '%short track%' THEN 'exhibition'
                WHEN event_name ILIKE '%rally%'        THEN 'rally'
                ELSE 'points'
            END
    """)
    )
    n = rowcount(result)
    logger.info("Classified event types for %d events", n)
    return n


def seed_conferences(session: Session) -> int:
    """Derive team→conference mapping from results data."""
    count = 0

    # Extract from results where conference field is populated
    rows = session.execute(
        text("""
        SELECT DISTINCT ri.team, e.season, r.conference
        FROM results r
        JOIN riders ri ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE r.conference IS NOT NULL
          AND r.conference != ''
          AND r.conference != 'Conference'
          AND ri.team IS NOT NULL
        ORDER BY e.season, r.conference, ri.team
    """)
    ).all()

    for team, season, conference in rows:
        # Normalize conference name (fix double spaces)
        conf = conference.strip()
        conf_group = CONFERENCE_LINEAGE.get(conf, conf)

        session.execute(
            text("""
            INSERT INTO team_conferences (team, season, conference, conference_group, source)
            VALUES (:team, :season, :conf, :group, 'derived')
            ON CONFLICT (team, season) DO UPDATE
            SET conference = :conf, conference_group = :group
        """),
            {"team": team, "season": season, "conf": conf, "group": conf_group},
        )
        count += 1

    logger.info("Seeded %d team-conference mappings", count)
    return count


def seed_all(session: Session) -> None:
    """Run all seed operations."""
    course_ids = seed_courses(session)
    map_events_to_courses(session, course_ids)
    seed_course_loops(session, course_ids)
    seed_division_laps(session, course_ids)
    normalize_divisions(session)
    classify_event_types(session)
    seed_season_profiles(session)
    seed_conferences(session)
    session.commit()
    # Record every fold above as lineage edges (ADR 002).
    from piclstats.quality.lineage import rebuild_all

    rebuild_all(session)
    logger.info("Seed complete")
