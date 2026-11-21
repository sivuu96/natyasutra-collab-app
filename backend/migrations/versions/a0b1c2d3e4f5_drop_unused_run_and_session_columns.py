"""drop columns nothing reads: a run's result-message link and a session's closed-at time

Revision ID: a0b1c2d3e4f5
Revises: f8a9b0c1d2e3
Create Date: 2026-11-22 10:00:00.000000

- agent_invocations.result_message_id: written with each reply, never read. The reply is found by its message.
- agent_sessions.closed_at: written when a session closes, never read. The status column already says closed.

Reversing this restores the empty columns. The values are not recreated.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_invocations") as batch:
        batch.drop_column("result_message_id")
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_column("closed_at")


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("agent_invocations") as batch:
        batch.add_column(sa.Column("result_message_id", sa.Uuid(), nullable=True))
