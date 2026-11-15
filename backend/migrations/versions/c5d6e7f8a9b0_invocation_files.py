"""files an agent returned in its reply

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-11-17 10:00:00.000000

A run can return up to five text files in its reply. Each file belongs to its run, so it is visible to whoever can
see the run (see app.services.files). The content is plain text, capped at 100,000 characters.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invocation_files",
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["agent_invocations.id"],
            name=op.f("fk_invocation_files_invocation_id_agent_invocations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocation_files")),
        sa.UniqueConstraint("invocation_id", "name", name="uq_invocation_files_name"),
    )
    op.create_index(op.f("ix_invocation_files_invocation_id"), "invocation_files", ["invocation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invocation_files_invocation_id"), table_name="invocation_files")
    op.drop_table("invocation_files")
