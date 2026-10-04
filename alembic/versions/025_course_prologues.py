"""Prologue distance per course and season

Revision ID: 025
Revises: 024
Create Date: 2026-10-03

Most PICL courses send the field over a short prologue (typically ~0.4 mi)
from the start onto the first lap, so lap 1 is longer than the loop and pace
computed as time / (laps x loop) comes out too slow. The prologue is shared by
MS and HS, so it lives per course and season (NULL season = course default,
same pattern as course_race_types), not on course_loops. No row means 0.0:
nothing changes until a distance is entered.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "course_prologues",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("season", sa.SmallInteger),  # NULL = default for every season
        sa.Column("prologue_miles", sa.Float, nullable=False),
        sa.CheckConstraint(
            "prologue_miles >= 0 AND prologue_miles < 5", name="ck_course_prologue_miles"
        ),
    )
    op.execute(
        "ALTER TABLE course_prologues ADD CONSTRAINT uq_course_prologue "
        "UNIQUE NULLS NOT DISTINCT (course_id, season)"
    )
    op.create_index("idx_course_prologues_course", "course_prologues", ["course_id"])


def downgrade() -> None:
    op.drop_index("idx_course_prologues_course", table_name="course_prologues")
    op.drop_table("course_prologues")
