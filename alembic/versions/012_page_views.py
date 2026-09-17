"""First-party usage log

Revision ID: 012
Revises: 011
Create Date: 2026-09-16

One row per page view, written by the app itself. No cookies, no third
party: the visitor is a salted hash of IP and browser that changes daily,
so uniques can be counted without storing anything identifying.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_views",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("route", sa.Text, nullable=False),  # 'rider' | 'team' | 'leaderboard' | ...
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("query", sa.Text),  # allow-listed params only
        sa.Column("entity", sa.Text),  # rider id, team name, event id, course id
        sa.Column("status", sa.SmallInteger, nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("visitor", sa.Text, nullable=False),  # salted daily hash, 16 hex chars
        sa.Column("user_id", sa.Integer),
        sa.Column("referrer", sa.Text),  # host only
        sa.Column("is_bot", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("idx_page_views_ts", "page_views", ["ts"])
    op.create_index("idx_page_views_route_ts", "page_views", ["route", "ts"])
    op.create_index("idx_page_views_visitor_ts", "page_views", ["visitor", "ts"])


def downgrade() -> None:
    op.drop_table("page_views")
