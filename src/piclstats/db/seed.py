"""Seed reference data: conferences, courses, division lap profiles."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

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

# Default loop distances (can be overridden per course later)
DEFAULT_LOOP_DISTANCES = {
    "MS": 2.0,   # ~2 miles (based on Granite, Hershey, Penn College actuals)
    "HS": 3.5,   # ~3.5 miles (based on Penn College, Hershey actuals)
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
        session.execute(text(
            "INSERT INTO courses (name, location) VALUES (:name, :loc) "
            "ON CONFLICT (name) DO NOTHING"
        ), {"name": name, "loc": info.get("location")})

    # Fetch IDs
    rows = session.execute(text("SELECT id, name FROM courses")).all()
    course_ids = {r[1]: r[0] for r in rows}
    logger.info("Seeded %d courses", len(course_ids))
    return course_ids


def map_events_to_courses(session: Session, course_ids: dict[str, int]) -> int:
    """Map events to courses based on event name patterns."""
    count = 0
    events = session.execute(text(
        "SELECT id, event_name FROM events WHERE course_id IS NULL"
    )).all()

    for event_id, event_name in events:
        for course_name, info in COURSES.items():
            if any(p.lower() in event_name.lower() for p in info["patterns"]):
                session.execute(text(
                    "UPDATE events SET course_id = :cid WHERE id = :eid"
                ), {"cid": course_ids[course_name], "eid": event_id})
                count += 1
                break

    logger.info("Mapped %d events to courses", count)
    return count


def seed_course_loops(session: Session, course_ids: dict[str, int]) -> int:
    """Seed MS and HS loops for every course with default distances."""
    count = 0
    for course_name, course_id in course_ids.items():
        for loop_type, distance in DEFAULT_LOOP_DISTANCES.items():
            session.execute(text("""
                INSERT INTO course_loops (course_id, loop_type, distance_miles)
                VALUES (:cid, :lt, :dist)
                ON CONFLICT (course_id, loop_type) DO UPDATE SET distance_miles = :dist
            """), {"cid": course_id, "lt": loop_type, "dist": distance})
            count += 1
    logger.info("Seeded %d course loops", count)
    return count


def seed_division_laps(session: Session, course_ids: dict[str, int]) -> int:
    """Seed division lap profiles for all courses."""
    count = 0
    for course_name, course_id in course_ids.items():
        for div, gender, laps, max_dur, cutoff, loop_type in DIVISION_PROFILES:
            session.execute(text("""
                INSERT INTO division_laps (course_id, division, gender, lap_count,
                    max_duration_mins, cutoff_mins, loop_type)
                VALUES (:cid, :div, :gender, :laps, :max_dur, :cutoff, :lt)
                ON CONFLICT (course_id, division, gender, season)
                DO UPDATE SET lap_count = :laps, max_duration_mins = :max_dur,
                    cutoff_mins = :cutoff, loop_type = :lt
            """), {
                "cid": course_id, "div": div, "gender": gender,
                "laps": laps, "max_dur": max_dur, "cutoff": cutoff,
                "lt": loop_type,
            })
            count += 1

    logger.info("Seeded %d division-lap profiles", count)
    return count


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
        result = session.execute(text(
            "UPDATE results SET division = :canon WHERE division = :alias"
        ), {"canon": canonical, "alias": alias})
        total += result.rowcount
    logger.info("Normalized %d result rows to canonical division labels", total)
    return total


def classify_event_types(session: Session) -> int:
    """Classify events as 'rally'/'exhibition' (non-scoring) by name pattern.

    Rallies and exhibition/short-track events do not count toward standings.
    Idempotent: re-derives event_type for every event from its name, so newly
    scraped events get classified on the next seed (like course mapping).
    """
    result = session.execute(text("""
        UPDATE events SET event_type =
            CASE
                WHEN event_name ILIKE '%exhibition%'
                  OR event_name ILIKE '%short track%' THEN 'exhibition'
                WHEN event_name ILIKE '%rally%'        THEN 'rally'
                ELSE 'points'
            END
    """))
    logger.info("Classified event types for %d events", result.rowcount)
    return result.rowcount


def seed_conferences(session: Session) -> int:
    """Derive team→conference mapping from results data."""
    count = 0

    # Extract from results where conference field is populated
    rows = session.execute(text("""
        SELECT DISTINCT ri.team, e.season, r.conference
        FROM results r
        JOIN riders ri ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE r.conference IS NOT NULL
          AND r.conference != ''
          AND r.conference != 'Conference'
          AND ri.team IS NOT NULL
        ORDER BY e.season, r.conference, ri.team
    """)).all()

    for team, season, conference in rows:
        # Normalize conference name (fix double spaces)
        conf = conference.strip()
        conf_group = CONFERENCE_LINEAGE.get(conf, conf)

        session.execute(text("""
            INSERT INTO team_conferences (team, season, conference, conference_group, source)
            VALUES (:team, :season, :conf, :group, 'derived')
            ON CONFLICT (team, season) DO UPDATE
            SET conference = :conf, conference_group = :group
        """), {"team": team, "season": season, "conf": conf, "group": conf_group})
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
    seed_conferences(session)
    session.commit()
    logger.info("Seed complete")
