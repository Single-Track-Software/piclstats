"""Event publish flag and discovered-events queue

Revision ID: 011
Revises: 010
Create Date: 2026-09-16

Nightly discovery loads new races automatically, so an event needs a
published flag the gate can leave false. discovered_events records every
raceresult id seen on the league results page and what happened to it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("is_published", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_table(
        "discovered_events",
        sa.Column("raceresult_id", sa.Integer, primary_key=True),
        sa.Column("season", sa.SmallInteger, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column(
            "found_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # 'new' | 'published' | 'blocked' | 'failed' | 'ignored'
        sa.Column("status", sa.Text, nullable=False, server_default="new"),
        sa.Column("note", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("discovered_events")
    op.drop_column("events", "is_published")
