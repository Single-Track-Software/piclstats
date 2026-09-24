"""Local dirt events: event kind, waves, course laps, riders without plates

Revision ID: 019
Revises: 018
Create Date: 2026-09-24

Local dirt races are informal team-vs-team events with no plates and no
segments: everyone in a wave starts on one countdown, the finish captain
taps as each rider crosses (the tap number is the lollipop stick), and the
rider's name is attached to the tap at the table. This adds the event kind,
waves (with the categories they hold), a laps count for the course, a wave
on each roster row and on wave-start crossings, and a public code for the
shareable results page. Roster "plates" for local dirt are internal numbers.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "timing_events", sa.Column("kind", sa.Text, nullable=False, server_default="rally")
    )
    op.create_check_constraint("ck_timing_event_kind", "timing_events", "kind IN ('rally', 'localdirt')")
    op.add_column("timing_events", sa.Column("laps", sa.SmallInteger, nullable=False, server_default="1"))
    op.add_column("timing_events", sa.Column("public_code", sa.Text, unique=True))
    op.create_table(
        "timing_waves",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timing_event_id",
            sa.Integer,
            sa.ForeignKey("timing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.SmallInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("categories", sa.Text),  # comma-separated roster categories this wave holds
        sa.UniqueConstraint("timing_event_id", "seq", name="uq_timing_wave_seq"),
    )
    op.add_column(
        "timing_roster",
        sa.Column("wave_id", sa.Integer, sa.ForeignKey("timing_waves.id", ondelete="SET NULL")),
    )
    op.add_column(
        "timing_crossings",
        sa.Column("wave_id", sa.Integer, sa.ForeignKey("timing_waves.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_column("timing_crossings", "wave_id")
    op.drop_column("timing_roster", "wave_id")
    op.drop_table("timing_waves")
    op.drop_column("timing_events", "public_code")
    op.drop_column("timing_events", "laps")
    op.drop_constraint("ck_timing_event_kind", "timing_events", type_="check")
    op.drop_column("timing_events", "kind")
