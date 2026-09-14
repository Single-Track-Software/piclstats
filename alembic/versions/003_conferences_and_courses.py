"""Add conference mapping, courses, and division lap profiles

Revision ID: 003
Revises: 002
Create Date: 2026-04-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Team → conference mapping per season
    op.create_table(
        "team_conferences",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("team", sa.Text, nullable=False),
        sa.Column("season", sa.SmallInteger, nullable=False),
        sa.Column("conference", sa.Text, nullable=False),
        sa.Column("conference_group", sa.Text),  # lineage grouping (e.g. "Eastern" for Blue+Gold)
        sa.Column("source", sa.Text, nullable=False),  # 'derived' or 'manual'
        sa.UniqueConstraint("team", "season", name="uq_team_conf_season"),
    )
    op.create_index("idx_team_conf_team", "team_conferences", ["team"])
    op.create_index("idx_team_conf_season", "team_conferences", ["season"])

    # Courses (venues)
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("location", sa.Text),
        sa.Column("distance_miles", sa.Float),  # per-lap distance
        sa.Column("elevation_ft", sa.Float),  # per-lap elevation gain
        sa.Column("difficulty_score", sa.Float),  # computed: f(distance, elevation)
        sa.Column("notes", sa.Text),
    )

    # Map events to courses
    op.add_column("events", sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id")))

    # Division lap profiles per course
    op.create_table(
        "division_laps",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("division", sa.Text, nullable=False),
        sa.Column("gender", sa.Text),
        sa.Column("lap_count", sa.SmallInteger, nullable=False),
        sa.Column("max_duration_mins", sa.SmallInteger),
        sa.Column("cutoff_mins", sa.SmallInteger),
        sa.Column("season", sa.SmallInteger),  # NULL = default, set for overrides
        sa.UniqueConstraint(
            "course_id", "division", "gender", "season", name="uq_div_laps_course_div"
        ),
    )
    op.create_index("idx_div_laps_course", "division_laps", ["course_id"])


def downgrade() -> None:
    op.drop_table("division_laps")
    op.drop_column("events", "course_id")
    op.drop_table("courses")
    op.drop_table("team_conferences")
