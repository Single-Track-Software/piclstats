"""Season schedule and race dates

Revision ID: 014
Revises: 013
Create Date: 2026-09-17

The forecast is gaining a "future races" table, which needs to know what is
coming: where each race is (lap counts come from the course profile) and who
it draws (a state race is the whole league, a conference race one conference).
Results only exist after a race, so the schedule is its own table, entered in
/admin/schedule. A scheduled race is "upcoming" purely by its date; nothing
links it to the event that results later load as, so the nightly pipeline is
untouched. Loaded events gain an optional date alongside, entered by hand.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("event_date", sa.Date))
    op.create_table(
        "scheduled_races",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("season", sa.SmallInteger, nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), nullable=False),
        # NULL = state race (every team); else team_conferences.conference
        sa.Column("conference", sa.Text),
        sa.UniqueConstraint("season", "event_date", "name", name="uq_scheduled_race"),
    )
    op.create_index("idx_scheduled_races_date", "scheduled_races", ["event_date"])


def downgrade() -> None:
    op.drop_table("scheduled_races")
    op.drop_column("events", "event_date")
