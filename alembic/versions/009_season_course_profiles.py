"""Per-season course loop and lap profiles

Revision ID: 009
Revises: 008
Create Date: 2026-09-14

Courses change year to year (re-routes, weather-shortened races), so loop
distance/elevation and per-division lap counts get a season dimension.
NULL season stays the default; a row for the event's season wins over it.

Both unique constraints become NULLS NOT DISTINCT so the default rows
(season NULL, and gender NULL for single-lap divisions) are actually unique
— under the old constraint every re-seed inserted a second copy.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("course_loops", sa.Column("season", sa.SmallInteger))

    op.drop_constraint("uq_course_loop", "course_loops", type_="unique")
    op.execute(
        "ALTER TABLE course_loops ADD CONSTRAINT uq_course_loop "
        "UNIQUE NULLS NOT DISTINCT (course_id, loop_type, season)"
    )

    op.drop_constraint("uq_div_laps_course_div", "division_laps", type_="unique")
    op.execute(
        "ALTER TABLE division_laps ADD CONSTRAINT uq_div_laps_course_div "
        "UNIQUE NULLS NOT DISTINCT (course_id, division, gender, season)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM division_laps WHERE season IS NOT NULL")
    op.drop_constraint("uq_div_laps_course_div", "division_laps", type_="unique")
    op.create_unique_constraint(
        "uq_div_laps_course_div", "division_laps", ["course_id", "division", "gender", "season"]
    )

    op.execute("DELETE FROM course_loops WHERE season IS NOT NULL")
    op.drop_constraint("uq_course_loop", "course_loops", type_="unique")
    op.create_unique_constraint("uq_course_loop", "course_loops", ["course_id", "loop_type"])
    op.drop_column("course_loops", "season")
