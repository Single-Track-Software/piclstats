"""Session version per user

Revision ID: 023
Revises: 022
Create Date: 2026-09-27

Sessions are signed cookies holding the user id, so until now nothing on
the server could invalidate one. The session records the user's version
at login; setting a new password (or "sign out everywhere") bumps it, and
every older cookie stops working at once.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer, nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("users", "session_version")
