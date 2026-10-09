"""Browser-confirmed page views

Revision ID: 028
Revises: 027
Create Date: 2026-10-09

page_views counted every request whose user-agent didn't look like a bot.
After Cloudflare, ~380 "visitors" a day were still mostly single home-page
hits round the clock: link previewers, prefetches and bots with browser
user-agents. Each HTML page view now carries a random view_id, embedded in
the page; a small script posts it back once a real browser shows the page
and someone interacts with it or keeps it visible for 4 s. The server only
accepts it for that view, from the same visitor, within 30 minutes, once.
The usage page counts people from confirmed views.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("page_views", sa.Column("view_id", sa.Text))
    op.add_column("page_views", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("page_views", sa.Column("confirmed_via", sa.Text))
    op.create_index(
        "uq_page_views_view_id",
        "page_views",
        ["view_id"],
        unique=True,
        postgresql_where=sa.text("view_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_page_views_view_id", table_name="page_views")
    op.drop_column("page_views", "confirmed_via")
    op.drop_column("page_views", "confirmed_at")
    op.drop_column("page_views", "view_id")
