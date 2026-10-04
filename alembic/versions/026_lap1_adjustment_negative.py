"""Allow a negative lap 1 adjustment

Revision ID: 026
Revises: 025
Create Date: 2026-10-04

course_prologues.prologue_miles is the extra distance on lap 1. Lap times
show it can be negative too: at Blue Mountain in 2024 and 2025 the start
joined the loop part-way, so lap 1 was ~0.5 mi short (lap 1 took 0.66-0.75x
lap 2 where a full loop takes ~0.97x). The admin calls it "Lap 1 adjustment";
the column keeps its name. Range widens from [0, 5) to (-2, 2).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_course_prologue_miles", "course_prologues", type_="check")
    op.create_check_constraint(
        "ck_course_prologue_miles", "course_prologues", "prologue_miles > -2 AND prologue_miles < 2"
    )


def downgrade() -> None:
    # Negative rows can't satisfy the old range; drop them (they fall back to 0).
    op.execute("DELETE FROM course_prologues WHERE prologue_miles < 0")
    op.drop_constraint("ck_course_prologue_miles", "course_prologues", type_="check")
    op.create_check_constraint(
        "ck_course_prologue_miles", "course_prologues", "prologue_miles >= 0 AND prologue_miles < 5"
    )
