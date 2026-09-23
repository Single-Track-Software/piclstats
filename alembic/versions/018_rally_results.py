"""Rally results: penalties, flag overrides, and events without a raceresult id

Revision ID: 018
Revises: 017
Create Date: 2026-09-23

A published rally is an ordinary events row, but it has no raceresult id, so
that column becomes nullable (the unique constraint already treats NULLs as
distinct). The lead can add a time penalty per rider (HS mechanical support
in a race segment, per the handbook) and accept a reconciliation flag with a
note so publishing can proceed; both are kept with who and when.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("events", "raceresult_id", nullable=True)
    op.create_table(
        "timing_adjustments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plate", sa.Integer, nullable=False),
        sa.Column(
            "segment_id", sa.Integer, sa.ForeignKey("timing_segments.id", ondelete="CASCADE")
        ),
        sa.Column("seconds", sa.Float, nullable=False),  # positive = penalty
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("idx_timing_adjustments_event", "timing_adjustments", ["timing_event_id"])
    op.create_table(
        "timing_flag_overrides",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("flag_key", sa.Text, nullable=False),  # e.g. 'start_no_finish:1547:3'
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("timing_event_id", "flag_key", name="uq_timing_flag_override"),
    )


def downgrade() -> None:
    op.drop_table("timing_flag_overrides")
    op.drop_index("idx_timing_adjustments_event", table_name="timing_adjustments")
    op.drop_table("timing_adjustments")
    op.execute(
        "DELETE FROM results WHERE event_id IN (SELECT id FROM events WHERE raceresult_id IS NULL)"
    )
    op.execute(
        "UPDATE timing_events SET published_event_id = NULL, status = 'approved' WHERE status = 'published'"
    )
    op.execute("DELETE FROM events WHERE raceresult_id IS NULL")
    op.alter_column("events", "raceresult_id", nullable=False)
