"""execution outbox

Revision ID: 5e0d1f2a3b4c
Revises: c2bea0dd1f66
Create Date: 2026-11-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e0d1f2a3b4c"
down_revision: str | Sequence[str] | None = "c2bea0dd1f66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_outbox",
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=80), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_execution_id", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'dead')", name=op.f("ck_execution_outbox_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["agent_invocations.id"],
            name=op.f("fk_execution_outbox_invocation_id_agent_invocations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_outbox")),
        sa.UniqueConstraint("dedupe_key", name="uq_execution_outbox_dedupe_key"),
    )
    op.create_index(op.f("ix_execution_outbox_invocation_id"), "execution_outbox", ["invocation_id"], unique=False)
    op.create_index(op.f("ix_execution_outbox_status"), "execution_outbox", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_execution_outbox_status"), table_name="execution_outbox")
    op.drop_index(op.f("ix_execution_outbox_invocation_id"), table_name="execution_outbox")
    op.drop_table("execution_outbox")
