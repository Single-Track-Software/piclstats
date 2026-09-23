"""SQLAlchemy Core table definitions."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
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
    # False while the publish gate holds a newly discovered race (ADR 002).
    Column("is_published", Boolean, nullable=False, server_default="true"),
    Column("scraped_at", DateTime(timezone=True), server_default=func.now()),
    # Race day, entered in /admin/schedule; the scrape doesn't carry one.
    Column("event_date", Date),
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
    # Derived blocking keys (quality.keys); raw name/team are never rewritten.
    Column("name_key", Text),
    Column("team_key", Text),
    UniqueConstraint("name", "team", name="uq_riders_name_team"),
    Index("idx_riders_name", "name"),
    Index("idx_riders_name_key", "name_key"),
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
    # 'ok' | 'warn' (kept, flagged) | 'excluded' (dropped from every stat)
    Column("dq_status", Text, nullable=False, server_default="ok"),
    UniqueConstraint("event_id", "bib", name="uq_results_event_bib"),
    Index("idx_results_rider", "rider_id"),
    Index("idx_results_category", "category"),
    Index("idx_results_event_category", "event_id", "category"),
    Index(
        "idx_results_conference", "conference", postgresql_where=Column("conference").isnot(None)
    ),
    Index("idx_results_dq_status", "dq_status", postgresql_where=Column("dq_status") != "ok"),
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
    Column("season", SmallInteger),  # NULL = default for every season
    UniqueConstraint(
        "course_id",
        "loop_type",
        "season",
        name="uq_course_loop",
        postgresql_nulls_not_distinct=True,
    ),
    Index("idx_course_loops_course", "course_id"),
)

# Whether a venue hosts a points race or a rally that season. NULL season is
# the course default; no row at all falls back to the event-name pattern.
# events.event_type is resolved from this by seed.classify_event_types.
course_race_types = Table(
    "course_race_types",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("course_id", Integer, ForeignKey("courses.id"), nullable=False),
    Column("season", SmallInteger),
    Column("race_type", Text, nullable=False),  # 'race' | 'rally'
    CheckConstraint("race_type IN ('race', 'rally')", name="ck_course_race_type"),
    UniqueConstraint(
        "course_id", "season", name="uq_course_race_type", postgresql_nulls_not_distinct=True
    ),
    Index("idx_course_race_types_course", "course_id"),
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
    UniqueConstraint(
        "course_id",
        "division",
        "gender",
        "season",
        name="uq_div_laps_course_div",
        postgresql_nulls_not_distinct=True,
    ),
    Index("idx_div_laps_course", "course_id"),
)

settings = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Login accounts for the gated features (staging / race position / prediction).
# 'member' unlocks those features; 'admin' also reaches the /admin config pages.
# email is stored lower-cased (normalized in code) so the unique constraint is
# effectively case-insensitive without needing the citext extension.
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False, server_default="member"),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
    Index("idx_users_email", "email"),
)

auth_tokens = Table(
    "auth_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # SHA-256 of the token, never the token itself.
    Column("token_hash", Text, nullable=False, unique=True),
    Column("purpose", Text, nullable=False),  # 'invite' | 'reset'
    Column("email", Text, nullable=False),
    Column("role", Text),  # invites only
    Column("user_id", Integer),  # resets only
    Column("created_by", Integer),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_auth_tokens_hash", "token_hash"),
    Index("idx_auth_tokens_email", "email"),
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


# ---------------------------------------------------------------------------
# Data-quality layer (ADR 002). Modelled on gvpd: one row per scrape run, row-
# level findings, an append-only lineage log, a long-format scorecard, and
# golden fixtures the publish gate checks against.

scrape_runs = Table(
    "scrape_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_id", Integer),  # set once the event row exists
    Column("raceresult_id", Integer, nullable=False),
    Column("season", SmallInteger),
    Column("started_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    # 'loaded' | 'checked' | 'published' | 'blocked' | 'failed'
    Column("status", Text, nullable=False, server_default="loaded"),
    Column("rows_parsed", Integer),
    Column("rows_loaded", Integer),
    Column("riders_new", Integer),
    Column("gate_passed", Boolean),
    Column("gate_reasons", JSONB),
    Column("detail", JSONB),  # detected column layout, list name, errors
    Index("idx_scrape_runs_event", "raceresult_id", "started_at"),
)

dq_checks = Table(
    "dq_checks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    Column("event_id", Integer, nullable=False),
    Column("result_id", Integer),  # NULL for event-level findings
    Column("check", Text, nullable=False),  # e.g. 'total_time_timestamp'
    Column("severity", Text, nullable=False),  # 'error' | 'warn' | 'info'
    Column("observed", Text),
    Column("expected", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_dq_checks_run", "run_id"),
    Index("idx_dq_checks_result", "result_id"),
    Index("idx_dq_checks_check", "check"),
)

# One typed edge per raw value folded into a canonical one. No FK to riders or
# results on purpose: deleting a row must not erase the decision log.
picl_lineage = Table(
    "picl_lineage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    # 'rider_merge' | 'team_norm' | 'division_norm' | 'event_classify' | 'course_map' | 'conference_norm'
    Column("stage", Text, nullable=False),
    Column("level", Text, nullable=False),  # 'rider' | 'team' | 'division' | 'event' | 'conference'
    Column("canonical_key", Text, nullable=False),
    Column("raw_value", Text, nullable=False),
    Column("raw_id", Integer),
    Column("source", Text, nullable=False),  # 'raceresult:<id>' | 'seed' | 'admin'
    Column("origin", Text),  # code path that decided, e.g. 'db/merge.py'
    # 'exact' | 'casing' | 'punctuation' | 'whitespace' | 'alias' | 'typo' | 'pattern' | 'manual'
    Column("mechanism", Text, nullable=False),
    Column("match_score", Float),
    Column("volume", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_lineage_level_key", "level", "canonical_key"),
    Index("idx_lineage_run", "run_id"),
    Index("idx_lineage_mechanism", "mechanism"),
)

picl_lineage_runs = Table(
    "picl_lineage_runs",
    metadata,
    Column("run_id", Integer, primary_key=True),
    Column("level", Text, nullable=False),
    Column("edges", Integer, nullable=False),
    Column("canonicals", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Long format: one row per metric per run so trends are a GROUP BY.
picl_dq_metrics = Table(
    "picl_dq_metrics",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    Column("metric", Text, nullable=False),
    Column("value", Float),
    Column("numerator", Integer),
    Column("denominator", Integer),
    Column("detail", JSONB),
    Column("captured_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_dq_metrics_metric", "metric", "captured_at"),
    Index("idx_dq_metrics_run", "run_id"),
)

# Positive fixtures: things that must stay true after every run.
picl_golden = Table(
    "picl_golden",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # 'rider_canonical' | 'event_type' | 'event_course' | 'division' | 'result_value'
    Column("kind", Text, nullable=False),
    Column("subject", JSONB, nullable=False),  # {"rider_id": 4766}
    Column("expected", JSONB, nullable=False),  # {"canonical_id": 4766}
    Column("note", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Negative fixtures: rider pairs that must never merge (same name, two kids).
picl_golden_pairs = Table(
    "picl_golden_pairs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("rider_id_a", Integer, nullable=False),
    Column("rider_id_b", Integer, nullable=False),
    Column("should_merge", Boolean, nullable=False, server_default="false"),
    Column("note", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("rider_id_a", "rider_id_b", name="uq_golden_pair"),
)

# Every raceresult id seen on the league results page and what became of it.
discovered_events = Table(
    "discovered_events",
    metadata,
    Column("raceresult_id", Integer, primary_key=True),
    Column("season", SmallInteger, nullable=False),
    Column("name", Text),
    Column("source_url", Text),
    Column("found_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column(
        "status", Text, nullable=False, server_default="new"
    ),  # new|published|blocked|failed|ignored
    Column("note", Text),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# First-party usage log (web/usage.py). visitor = salted daily hash of IP + UA.
page_views = Table(
    "page_views",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("route", Text, nullable=False),
    Column("path", Text, nullable=False),
    Column("query", Text),
    Column("entity", Text),
    Column("status", SmallInteger, nullable=False),
    Column("duration_ms", Integer),
    Column("visitor", Text, nullable=False),
    Column("user_id", Integer),
    Column("referrer", Text),
    Column("is_bot", Boolean, nullable=False, server_default="false"),
    Index("idx_page_views_ts", "ts"),
    Index("idx_page_views_route_ts", "route", "ts"),
    Index("idx_page_views_visitor_ts", "visitor", "ts"),
)

# The season calendar (/admin/schedule). A race is "upcoming" by date alone;
# it is never linked to the event its results later load as.
scheduled_races = Table(
    "scheduled_races",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("season", SmallInteger, nullable=False),
    Column("event_date", Date, nullable=False),
    Column("name", Text, nullable=False),
    Column("course_id", Integer, ForeignKey("courses.id"), nullable=False),
    Column("conference", Text),  # NULL = state race; else team_conferences.conference
    UniqueConstraint("season", "event_date", "name", name="uq_scheduled_race"),
    Index("idx_scheduled_races_date", "event_date"),
)


# ---------------------------------------------------------------------------
# Rally timing (ADR 005). A timing lead sets up an event with segments, each
# with a start and a finish point; volunteers join a point by its station
# code and record crossings on their phones. Crossings are append-only: a
# correction is a new row that supersedes the old one.

timing_events = Table(
    "timing_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("season", SmallInteger, nullable=False),
    Column("name", Text, nullable=False),
    Column("event_date", Date),
    Column("course_id", Integer, ForeignKey("courses.id")),
    Column("status", Text, nullable=False, server_default="setup"),  # setup|live|approved|published
    Column("created_by", Integer),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("published_event_id", Integer, ForeignKey("events.id")),
    UniqueConstraint("season", "name", name="uq_timing_event"),
)

timing_segments = Table(
    "timing_segments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "timing_event_id",
        Integer,
        ForeignKey("timing_events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("seq", SmallInteger, nullable=False),
    Column("name", Text, nullable=False),
    Column("distance_miles", Float),
    Column("elevation_ft", Float),
    Column("rides_hs", Boolean, nullable=False, server_default="true"),  # HS rides this segment
    Column("rides_ms", Boolean, nullable=False, server_default="true"),  # MS rides this segment
    UniqueConstraint("timing_event_id", "seq", name="uq_timing_segment_seq"),
)

timing_points = Table(
    "timing_points",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "segment_id", Integer, ForeignKey("timing_segments.id", ondelete="CASCADE"), nullable=False
    ),
    Column("kind", Text, nullable=False),  # 'start' | 'finish'
    Column("station_code", Text, nullable=False, unique=True),
    CheckConstraint("kind IN ('start', 'finish')", name="ck_timing_point_kind"),
    UniqueConstraint("segment_id", "kind", name="uq_timing_point"),
)

timing_roster = Table(
    "timing_roster",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "timing_event_id",
        Integer,
        ForeignKey("timing_events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("plate", Integer, nullable=False),
    Column("name", Text, nullable=False),
    Column("team", Text),
    Column("category", Text),
    Column("source", Text, nullable=False),  # 'paste' | 'season' | 'manual'
    UniqueConstraint("timing_event_id", "plate", name="uq_timing_roster_plate"),
)

timing_devices = Table(
    "timing_devices",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "timing_event_id",
        Integer,
        ForeignKey("timing_events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("point_id", Integer, ForeignKey("timing_points.id"), nullable=False),
    Column("label", Text),
    Column("user_agent", Text),
    Column("joined_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("offset_ms", Integer),
    Column("offset_drift_ms", Integer, nullable=False, server_default="0"),
    Column("pending", Integer, nullable=False, server_default="0"),
    Index("idx_timing_devices_event", "timing_event_id"),
)

timing_crossings = Table(
    "timing_crossings",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "timing_event_id",
        Integer,
        ForeignKey("timing_events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("point_id", Integer, ForeignKey("timing_points.id"), nullable=False),
    Column("device_id", Text),
    Column("device_ts", DateTime(timezone=True), nullable=False),
    Column("offset_ms", Integer, nullable=False, server_default="0"),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("plate", Integer),
    Column("kind", Text, nullable=False),  # 'tap' | 'manual' | 'correction'
    Column("supersedes", Text),
    Column("voided", Boolean, nullable=False, server_default="false"),
    Column("note", Text),
    Column("author", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("kind IN ('tap', 'manual', 'correction')", name="ck_timing_crossing_kind"),
    Index("idx_timing_crossings_point", "timing_event_id", "point_id"),
    Index("idx_timing_crossings_supersedes", "supersedes"),
)
