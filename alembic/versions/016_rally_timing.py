"""Rally timing: events, segments, points, roster, devices, crossings

Revision ID: 016
Revises: 015
Create Date: 2026-09-23

PICL rallies are timed by volunteers at each segment's start and finish
(docs/requirements/PICL Rally Timing — Requirements.pdf, ADR 005). These
tables hold the setup a timing lead enters, the roster of plates, every
device that joined a station, and the append-only log of crossings each
device records. Nothing here touches the race-results tables until a rally
is approved and published.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "timing_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("season", sa.SmallInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("event_date", sa.Date),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id")),
        # 'setup' -> 'live' -> 'approved' -> 'published'
        sa.Column("status", sa.Text, nullable=False, server_default="setup"),
        sa.Column("created_by", sa.Integer),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # The events row the approved results were published as.
        sa.Column("published_event_id", sa.Integer, sa.ForeignKey("events.id")),
        sa.UniqueConstraint("season", "name", name="uq_timing_event"),
    )
    op.create_table(
        "timing_segments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.SmallInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("distance_miles", sa.Float),
        sa.Column("elevation_ft", sa.Float),
        # Which groups ride this segment. HS may ride more segments than MS;
        # a rider's total is the sum of the segments their group rides.
        sa.Column("rides_hs", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("rides_ms", sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("timing_event_id", "seq", name="uq_timing_segment_seq"),
    )
    op.create_table(
        "timing_points",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "segment_id",
            sa.Integer,
            sa.ForeignKey("timing_segments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),  # 'start' | 'finish'
        # The join code printed on the station's QR sheet; unguessable, no account.
        sa.Column("station_code", sa.Text, nullable=False, unique=True),
        sa.CheckConstraint("kind IN ('start', 'finish')", name="ck_timing_point_kind"),
        sa.UniqueConstraint("segment_id", "kind", name="uq_timing_point"),
    )
    op.create_table(
        "timing_roster",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plate", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("team", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("source", sa.Text, nullable=False),  # 'paste' | 'season' | 'manual'
        sa.UniqueConstraint("timing_event_id", "plate", name="uq_timing_roster_plate"),
    )
    op.create_table(
        "timing_devices",
        sa.Column("id", sa.Text, primary_key=True),  # client-generated uuid
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("point_id", sa.Integer, sa.ForeignKey("timing_points.id"), nullable=False),
        sa.Column("label", sa.Text),  # volunteer's name, optional
        sa.Column("user_agent", sa.Text),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        # Device clock minus server clock at the last sync, and the largest
        # change seen between two syncs (the clock-trust signal, N2).
        sa.Column("offset_ms", sa.Integer),
        sa.Column("offset_drift_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pending", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("idx_timing_devices_event", "timing_devices", ["timing_event_id"])
    op.create_table(
        "timing_crossings",
        sa.Column("id", sa.Text, primary_key=True),  # client-generated uuid
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("point_id", sa.Integer, sa.ForeignKey("timing_points.id"), nullable=False),
        sa.Column("device_id", sa.Text),  # NULL for records the lead enters by hand
        sa.Column("device_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offset_ms", sa.Integer, nullable=False, server_default="0"),
        # device_ts corrected by the device's clock offset: what results use.
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plate", sa.Integer),
        # 'tap' (recorded live) | 'manual' (backup sheet) | 'correction' (supersedes another)
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("supersedes", sa.Text),  # the crossing this one replaces; never edited in place
        sa.Column("voided", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("note", sa.Text),
        sa.Column("author", sa.Text, nullable=False),  # device id or user email
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('tap', 'manual', 'correction')", name="ck_timing_crossing_kind"
        ),
    )
    op.create_index(
        "idx_timing_crossings_point", "timing_crossings", ["timing_event_id", "point_id"]
    )
    op.create_index("idx_timing_crossings_supersedes", "timing_crossings", ["supersedes"])


def downgrade() -> None:
    op.drop_table("timing_crossings")
    op.drop_table("timing_devices")
    op.drop_table("timing_roster")
    op.drop_table("timing_points")
    op.drop_table("timing_segments")
    op.drop_table("timing_events")
