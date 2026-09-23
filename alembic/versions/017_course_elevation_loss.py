"""Elevation loss per course loop

Revision ID: 017
Revises: 016
Create Date: 2026-09-23

course_loops.elevation_ft is the climb per lap. Descent is as telling a
metric for a course (and the whole story for a rally segment), so each loop
row gains elevation_loss_ft alongside it, per season like the rest.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("course_loops", sa.Column("elevation_loss_ft", sa.Float))


def downgrade() -> None:
    op.drop_column("course_loops", "elevation_loss_ft")
