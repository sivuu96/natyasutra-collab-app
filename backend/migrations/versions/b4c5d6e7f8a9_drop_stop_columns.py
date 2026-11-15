"""drop the outbox columns that only the stop step used

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-16 10:00:00.000000

The stop step asked the job provider to cancel an execution. Runs now go to the Cloud Run service, which needs no
stop call, so nothing reads these columns.

- execution_outbox.provider_execution_id: the execution name recorded for the stop step. The lease keeps its own
  binding to the execution, which is still used.
- execution_outbox.cancel_sent_at: set once the stop step had run.

Reversing this restores the empty columns. The values are not recreated.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("execution_outbox") as batch:
        batch.drop_column("cancel_sent_at")
        batch.drop_column("provider_execution_id")


def downgrade() -> None:
    with op.batch_alter_table("execution_outbox") as batch:
        batch.add_column(sa.Column("provider_execution_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("cancel_sent_at", sa.DateTime(timezone=True), nullable=True))
