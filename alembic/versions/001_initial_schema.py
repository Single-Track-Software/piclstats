"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("raceresult_id", sa.Integer, nullable=False, unique=True),
        sa.Column("season", sa.SmallInteger, nullable=False),
        sa.Column("event_name", sa.Text, nullable=False),
        sa.Column("event_order", sa.SmallInteger),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_events_season", "events", ["season"])

    op.create_table(
        "riders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("team", sa.Text),
        sa.Column("school", sa.Text),
        sa.UniqueConstraint("name", "team", name="uq_riders_name_team"),
    )
    op.create_index("idx_riders_name", "riders", ["name"])

    op.create_table(
        "results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("rider_id", sa.Integer, sa.ForeignKey("riders.id"), nullable=False),
        sa.Column("bib", sa.Integer, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("category_order", sa.SmallInteger),
        sa.Column("gender", sa.Text),
        sa.Column("division", sa.Text),
        sa.Column("place", sa.SmallInteger),
        sa.Column("status", sa.Text),
        sa.Column("points", sa.SmallInteger),
        sa.Column("conference", sa.Text),
        sa.Column("lap1", sa.Interval),
        sa.Column("lap2", sa.Interval),
        sa.Column("lap3", sa.Interval),
        sa.Column("lap4", sa.Interval),
        sa.Column("lap5", sa.Interval),
        sa.Column("lap6", sa.Interval),
        sa.Column("penalty", sa.Interval),
        sa.Column("total_time", sa.Interval),
        sa.Column("total_time_raw", sa.Text, nullable=False),
        sa.Column("raw_data", JSONB),
        sa.UniqueConstraint("event_id", "bib", name="uq_results_event_bib"),
    )
    op.create_index("idx_results_rider", "results", ["rider_id"])
    op.create_index("idx_results_category", "results", ["category"])
    op.create_index("idx_results_event_category", "results", ["event_id", "category"])
    op.create_index(
        "idx_results_conference",
        "results",
        ["conference"],
        postgresql_where=sa.text("conference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("results")
    op.drop_table("riders")
    op.drop_table("events")
