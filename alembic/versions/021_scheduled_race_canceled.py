"""Canceled scheduled races

Revision ID: 021
Revises: 020
Create Date: 2026-09-27

A race called off (rain) and not rescheduled stays on the schedule as
canceled, so the page and the calendar feed say so, but it no longer
counts as an upcoming race for forecasts, sheets or the planner.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scheduled_races",
        sa.Column("canceled", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("scheduled_races", "canceled")
