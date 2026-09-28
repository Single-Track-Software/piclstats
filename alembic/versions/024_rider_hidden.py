"""Hidden riders (privacy requests)

Revision ID: 024
Revises: 023
Create Date: 2026-09-28

A family may ask for a rider not to have a profile on the site. Hidden
riders keep their rows in the league's published results, but have no
rider page, no search listing, no place in leaderboards, rosters, rivals
or the sitemap.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "riders", sa.Column("hidden", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.add_column("riders", sa.Column("hidden_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("riders", "hidden_at")
    op.drop_column("riders", "hidden")
