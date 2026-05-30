"""Add event_type to classify scoring vs non-scoring events

Rallies and exhibitions/short-track events do not count toward league
standings. This adds events.event_type ('points' | 'rally' | 'exhibition')
and classifies existing rows by name pattern.

Revision ID: 006
Revises: 005
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "event_type",
            sa.Text,
            nullable=False,
            server_default="points",
        ),
    )
    op.create_index("idx_events_event_type", "events", ["event_type"])

    # Classify existing events by name pattern. Exhibition/short-track checked
    # first so "Exhibition - Johnstown Short Track" lands as 'exhibition'.
    op.execute(
        """
        UPDATE events SET event_type =
            CASE
                WHEN event_name ILIKE '%exhibition%'
                  OR event_name ILIKE '%short track%' THEN 'exhibition'
                WHEN event_name ILIKE '%rally%'        THEN 'rally'
                ELSE 'points'
            END
        """
    )


def downgrade() -> None:
    op.drop_index("idx_events_event_type", table_name="events")
    op.drop_column("events", "event_type")
