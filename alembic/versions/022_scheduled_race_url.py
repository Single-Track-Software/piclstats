"""Scheduled race link

Revision ID: 022
Revises: 021
Create Date: 2026-09-27

A link per scheduled race (the league's page for it on pamtb.org), shown on
the public schedule and carried in the calendar feed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scheduled_races", sa.Column("url", sa.Text))


def downgrade() -> None:
    op.drop_column("scheduled_races", "url")
