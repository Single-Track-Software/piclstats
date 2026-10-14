"""TTC bonus points

Revision ID: 029
Revises: 028
Create Date: 2026-10-13

Conference standings add each rider's Teen Trail Corps bonus to the series
score (PA handbook 5.15). The bonus comes from a PitZone-fed spreadsheet the
league loads into raceresult; piclstats reads it from raceresult's
season-overall lists (`piclstats ttc-sync`) into this table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ttc_bonus",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("season", sa.SmallInteger, nullable=False),
        sa.Column("rider_id", sa.Integer, sa.ForeignKey("riders.id"), nullable=False),
        sa.Column("division", sa.Text, nullable=False),
        sa.Column("gender", sa.Text, nullable=False),
        sa.Column("bib", sa.Integer, nullable=False),
        sa.Column("bonus_points", sa.Integer, nullable=False),
        sa.Column("hours", sa.Numeric(6, 2)),
        sa.Column("raceresult_id", sa.Integer, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("season", "rider_id", "division", "gender", name="uq_ttc_bonus_rider"),
    )


def downgrade() -> None:
    op.drop_table("ttc_bonus")
