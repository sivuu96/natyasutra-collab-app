"""drop columns nothing reads: agent description, platform admin flag, session last-seen time

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-13 10:00:00.000000

- agents.description: set only by the removed bridge; no screen or API consumer uses it.
- users.is_platform_admin: never set to true and never read.
- auth_sessions.last_seen_at: written on each request but never read; the idle timeout uses idle_expires_at.

This migration cannot be reversed: the dropped values are not recreated.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("agents", "description")
    op.drop_column("users", "is_platform_admin")
    op.drop_column("auth_sessions", "last_seen_at")


def downgrade() -> None:
    raise NotImplementedError("These columns were unused and their values were dropped. They cannot be restored.")
