"""Race type per course and season

Revision ID: 015
Revises: 014
Create Date: 2026-09-21

Whether a venue hosts a points race or a rally has been read off the event
name ('%rally%'). That is fragile — the 2026 Birdsboro rally is named
"Central #1" — and there is nowhere to say so before the results load. The
flag now lives on the course profile, per season with a NULL-season default
(ADR 001), and events.event_type is resolved from it. Exhibitions stay a
name pattern: Johnstown 2024 hosted both a points race and a short-track
exhibition in the same season.

Backfill: every course-season that already has events gets an explicit row
from the current classification, so nothing changes on deploy.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "course_race_types",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("season", sa.SmallInteger),  # NULL = default for every season
        sa.Column("race_type", sa.Text, nullable=False),  # 'race' | 'rally'
        sa.CheckConstraint("race_type IN ('race', 'rally')", name="ck_course_race_type"),
    )
    op.execute(
        "ALTER TABLE course_race_types ADD CONSTRAINT uq_course_race_type "
        "UNIQUE NULLS NOT DISTINCT (course_id, season)"
    )
    op.create_index("idx_course_race_types_course", "course_race_types", ["course_id"])
    op.execute(
        """
        INSERT INTO course_race_types (course_id, season, race_type)
        SELECT e.course_id, e.season,
               CASE WHEN bool_or(e.event_type = 'rally') THEN 'rally' ELSE 'race' END
        FROM events e
        WHERE e.course_id IS NOT NULL AND e.season > 0
        GROUP BY e.course_id, e.season
        """
    )


def downgrade() -> None:
    op.drop_index("idx_course_race_types_course", table_name="course_race_types")
    op.drop_table("course_race_types")
