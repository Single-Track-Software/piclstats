"""Roles: coach, picl, admin

Revision ID: 013
Revises: 012
Create Date: 2026-09-16

'member' had everything short of admin. It splits into 'coach' (finish-time
predictions) and 'picl' (predictions plus staging). Existing members keep
what they had, so they become 'picl'; admins downgrade individual coaches
from /admin/users.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'picl' WHERE role = 'member'")
    op.execute("UPDATE auth_tokens SET role = 'picl' WHERE role = 'member'")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'member' WHERE role IN ('coach', 'picl')")
    op.execute("UPDATE auth_tokens SET role = 'member' WHERE role IN ('coach', 'picl')")
