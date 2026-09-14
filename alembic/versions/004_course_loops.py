"""Add course loops (MS/HS per venue) and link divisions to loops

Revision ID: 004
Revises: 003
Create Date: 2026-04-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "course_loops",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("loop_type", sa.Text, nullable=False),  # 'MS' or 'HS'
        sa.Column("distance_miles", sa.Float),
        sa.Column("elevation_ft", sa.Float),
        sa.UniqueConstraint("course_id", "loop_type", name="uq_course_loop"),
    )
    op.create_index("idx_course_loops_course", "course_loops", ["course_id"])

    # Link each division to a loop type
    op.add_column("division_laps", sa.Column("loop_type", sa.Text))


def downgrade() -> None:
    op.drop_column("division_laps", "loop_type")
    op.drop_table("course_loops")
