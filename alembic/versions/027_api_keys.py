"""API keys for read-only, team-scoped access

Revision ID: 027
Revises: 026
Create Date: 2026-10-08

A key lets an automated client (first: a Team Director's news agent) read
/api/v1 for its own teams only. Like auth tokens, only the SHA-256 hash is
stored; the raw key is shown once at creation. `team_keys` are normalised
team keys (quality.keys.team_key) so every published spelling of a team
matches; `team_names` keep what the admin picked, for display.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column("key_hash", sa.Text, nullable=False, unique=True),
        sa.Column("team_names", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("team_keys", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
