"""invocation leases

Revision ID: 9b2c3d4e5f60
Revises: 7a1c2d3e4f50
Create Date: 2026-11-11 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b2c3d4e5f60"
down_revision: str | Sequence[str] | None = "7a1c2d3e4f50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invocation_leases",
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_execution_id", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["agent_invocations.id"],
            name=op.f("fk_invocation_leases_invocation_id_agent_invocations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocation_leases")),
        sa.UniqueConstraint("token_hash", name="uq_invocation_leases_token_hash"),
    )
    op.create_index(op.f("ix_invocation_leases_invocation_id"), "invocation_leases", ["invocation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invocation_leases_invocation_id"), table_name="invocation_leases")
    op.drop_table("invocation_leases")
