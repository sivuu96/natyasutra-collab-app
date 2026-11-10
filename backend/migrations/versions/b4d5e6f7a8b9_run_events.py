"""run events

Revision ID: b4d5e6f7a8b9
Revises: 9b2c3d4e5f60
Create Date: 2026-11-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "9b2c3d4e5f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["agent_invocations.id"],
            name=op.f("fk_run_events_invocation_id_agent_invocations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
        sa.UniqueConstraint("invocation_id", "sequence", name="uq_run_events_invocation_sequence"),
    )
    op.create_index(op.f("ix_run_events_invocation_id"), "run_events", ["invocation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_run_events_invocation_id"), table_name="run_events")
    op.drop_table("run_events")
