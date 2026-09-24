"""Local dirt results

Revision ID: 020
Revises: 019
Create Date: 2026-09-24

Published local dirt results stay out of the official results tables: one
row per rider per event here, linked to the riders table so a rider's page
can show them, and served on a shareable page under the event's public code.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "local_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rider_id", sa.Integer, sa.ForeignKey("riders.id"), nullable=False),
        sa.Column("roster_plate", sa.Integer, nullable=False),  # the internal roster number
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("team", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("wave", sa.Text),
        sa.Column("place_wave", sa.SmallInteger),  # finish order within the wave
        sa.Column("place_category", sa.SmallInteger),  # within the category
        sa.Column("laps", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("elapsed_seconds", sa.Float),  # last crossing minus the wave start
        sa.Column("status", sa.Text, nullable=False),  # 'OK' | 'DNF'
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("timing_event_id", "roster_plate", name="uq_local_result"),
    )
    op.create_index("idx_local_results_rider", "local_results", ["rider_id"])


def downgrade() -> None:
    op.drop_index("idx_local_results_rider", table_name="local_results")
    op.drop_table("local_results")
