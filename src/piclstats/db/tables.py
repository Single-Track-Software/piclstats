"""SQLAlchemy Core table definitions."""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Interval,
    MetaData,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("raceresult_id", Integer, nullable=False, unique=True),
    Column("season", SmallInteger, nullable=False),
    Column("event_name", Text, nullable=False),
    Column("event_order", SmallInteger),
    # 'points' (counts toward standings) | 'rally' | 'exhibition' (do not)
    Column("event_type", Text, nullable=False, server_default="points"),
    Column("scraped_at", DateTime(timezone=True), server_default=func.now()),
    Index("idx_events_season", "season"),
    Index("idx_events_event_type", "event_type"),
)

riders = Table(
    "riders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("team", Text),
    Column("school", Text),
    UniqueConstraint("name", "team", name="uq_riders_name_team"),
    Index("idx_riders_name", "name"),
)

results = Table(
    "results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_id", Integer, nullable=False),
    Column("rider_id", Integer, nullable=False),
    Column("bib", Integer, nullable=False),
    Column("category", Text, nullable=False),
    Column("category_order", SmallInteger),
    Column("gender", Text),
    Column("division", Text),
    Column("place", SmallInteger),
    Column("status", Text),
    Column("points", SmallInteger),
    Column("conference", Text),
    Column("lap1", Interval),
    Column("lap2", Interval),
    Column("lap3", Interval),
    Column("lap4", Interval),
    Column("lap5", Interval),
    Column("lap6", Interval),
    Column("penalty", Interval),
    Column("total_time", Interval),
    Column("total_time_raw", Text, nullable=False),
    Column("raw_data", JSONB),
    UniqueConstraint("event_id", "bib", name="uq_results_event_bib"),
    Index("idx_results_rider", "rider_id"),
    Index("idx_results_category", "category"),
    Index("idx_results_event_category", "event_id", "category"),
    Index("idx_results_conference", "conference", postgresql_where=Column("conference").isnot(None)),
)

team_conferences = Table(
    "team_conferences",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("team", Text, nullable=False),
    Column("season", SmallInteger, nullable=False),
    Column("conference", Text, nullable=False),
    Column("conference_group", Text),  # lineage: "Eastern" for Blue+Gold
    Column("source", Text, nullable=False),
    UniqueConstraint("team", "season", name="uq_team_conf_season"),
    Index("idx_team_conf_team", "team"),
    Index("idx_team_conf_season", "season"),
)

courses = Table(
    "courses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, unique=True),
    Column("location", Text),
    Column("distance_miles", Float),
    Column("elevation_ft", Float),
    Column("difficulty_score", Float),
    Column("notes", Text),
)

course_loops = Table(
    "course_loops",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("course_id", Integer, nullable=False),
    Column("loop_type", Text, nullable=False),  # 'MS' or 'HS'
    Column("distance_miles", Float),
    Column("elevation_ft", Float),
    UniqueConstraint("course_id", "loop_type", name="uq_course_loop"),
    Index("idx_course_loops_course", "course_id"),
)

division_laps = Table(
    "division_laps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("course_id", Integer, nullable=False),
    Column("division", Text, nullable=False),
    Column("gender", Text),
    Column("lap_count", SmallInteger, nullable=False),
    Column("max_duration_mins", SmallInteger),
    Column("cutoff_mins", SmallInteger),
    Column("season", SmallInteger),
    UniqueConstraint("course_id", "division", "gender", "season",
                     name="uq_div_laps_course_div"),
    Index("idx_div_laps_course", "course_id"),
)

settings = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

rider_aliases = Table(
    "rider_aliases",
    metadata,
    Column("rider_id", Integer, primary_key=True),
    Column("canonical_id", Integer, nullable=False),
    Column("match_method", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Index("idx_aliases_canonical", "canonical_id"),
)
