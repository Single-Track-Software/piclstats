"""Per-event conference series flag

Revision ID: 030
Revises: 029
Create Date: 2026-10-14

A rally posts series points, but the league doesn't always count them: in
2026 the Eastern Gold rally counts in its series and the Central rally
scores 0 for everyone (raceresult's season lists). NULL keeps the default
(a points race counts, a rally doesn't); `piclstats load-rally
--counts-in-series` sets it for a rally the league scored.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("counts_in_series", sa.Boolean))


def downgrade() -> None:
    op.drop_column("events", "counts_in_series")
