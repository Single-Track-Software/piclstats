"""Add rider_aliases for merging duplicate riders

Revision ID: 002
Revises: 001
Create Date: 2026-04-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rider_aliases",
        sa.Column("rider_id", sa.Integer, sa.ForeignKey("riders.id"), primary_key=True),
        sa.Column("canonical_id", sa.Integer, sa.ForeignKey("riders.id"), nullable=False),
        sa.Column("match_method", sa.Text, nullable=False),  # 'auto_name' or 'manual'
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_aliases_canonical", "rider_aliases", ["canonical_id"])


def downgrade() -> None:
    op.drop_table("rider_aliases")
