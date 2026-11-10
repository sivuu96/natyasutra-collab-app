"""run activity and cancel

Revision ID: d6f7a8b9c0d1
Revises: c5e6f7a8b9c0
Create Date: 2026-11-11 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "c5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_invocations") as batch:
        batch.add_column(sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("execution_outbox") as batch:
        batch.add_column(sa.Column("cancel_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("execution_outbox") as batch:
        batch.drop_column("cancel_sent_at")
    with op.batch_alter_table("agent_invocations") as batch:
        batch.drop_column("last_activity_at")
