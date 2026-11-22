"""session workspace: one summary row per saved turn (file tree, changes, diff)

Revision ID: a1b2c3d4e5f6
Revises: b1c2d3e4f5a6
Create Date: 2026-11-23 09:00:00.000000

The runtime sends a summary after each successful turn of a session. The session screen shows the tree and the latest
changes from these rows. The full files stay in the session's saved state, not in this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_workspace_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "invocation_id",
            sa.Uuid(),
            sa.ForeignKey("agent_invocations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        # The files in the folder after this turn: [{"path": ..., "size": ...}]
        sa.Column("files", sa.JSON(), nullable=False),
        # What this turn changed: [{"path": ..., "change": "added" | "changed" | "removed"}]
        sa.Column("changes", sa.JSON(), nullable=False),
        # The diff of this turn, shortened to a fixed size when it is larger.
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column("diff_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_session_workspace_turns_session_id", "session_workspace_turns", ["session_id"])
    op.create_index("ix_session_workspace_turns_project_id", "session_workspace_turns", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_session_workspace_turns_project_id", table_name="session_workspace_turns")
    op.drop_index("ix_session_workspace_turns_session_id", table_name="session_workspace_turns")
    op.drop_table("session_workspace_turns")
