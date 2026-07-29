"""Add auth_tokens table for invite and password-reset links

Revision ID: 008
Revises: 007
Create Date: 2026-07-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # SHA-256 of the token, never the token itself: a database leak must not
        # hand over working invite or reset links.
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        # 'invite' (no account yet) | 'reset' (existing account)
        sa.Column("purpose", sa.Text, nullable=False),
        # Invites bind the address they were issued for so the recipient can't
        # redirect the invite to a different account.
        sa.Column("email", sa.Text, nullable=False),
        # Role the invite grants; null for resets.
        sa.Column("role", sa.Text),
        # Target account for resets; null for invites (the user doesn't exist yet).
        sa.Column("user_id", sa.Integer),
        sa.Column("created_by", sa.Integer),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_auth_tokens_hash", "auth_tokens", ["token_hash"])
    op.create_index("idx_auth_tokens_email", "auth_tokens", ["email"])


def downgrade() -> None:
    op.drop_index("idx_auth_tokens_email", table_name="auth_tokens")
    op.drop_index("idx_auth_tokens_hash", table_name="auth_tokens")
    op.drop_table("auth_tokens")
