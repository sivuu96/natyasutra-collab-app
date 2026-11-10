"""remove the local bridge: devices, pairing, approvals, inbound events, Codex, and bridge-only columns

Revision ID: f2a3b4c5d6e7
Revises: e7a8b9c0d1e2
Create Date: 2026-11-12 10:00:00.000000

PostgreSQL only (production). Refuses to run while any agent still belongs to a local device: those agents
cannot run any more, so decide what to do with them first (for example, delete them in the hub UI or by SQL).
Check with: SELECT count(*) FROM agents WHERE device_id IS NOT NULL;

This migration cannot be reversed: the dropped tables hold data that is not recreated.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    device_agents = bind.execute(sa.text("SELECT count(*) FROM agents WHERE device_id IS NOT NULL")).scalar_one()
    if device_agents:
        raise RuntimeError(
            f"{device_agents} agent(s) still belong to a local bridge device. Remove or convert them before "
            "running this migration (see the docstring)."
        )

    # Runs that were in flight in the removed states. A cloud run that had been submitted is now accepted-or-queued.
    op.execute("UPDATE agent_invocations SET status = 'accepted' WHERE status = 'delivered'")
    op.execute(
        "UPDATE agent_invocations SET status = 'failed', error_code = COALESCE(error_code, 'bridge_removed'), "
        "completed_at = COALESCE(completed_at, now()) WHERE status IN ('awaiting_approval', 'rejected')"
    )

    # Columns that reference the bridge tables go first, so the tables can be dropped.
    op.drop_constraint(op.f("ck_agents_harness_valid"), "agents", type_="check")
    op.drop_column("agents", "device_id")
    op.drop_column("agents", "capabilities")
    op.create_check_constraint(op.f("ck_agents_harness_valid"), "agents", "harness IN ('claude_code')")

    op.drop_table("agent_events")
    op.drop_table("agent_approvals")
    op.drop_table("bridge_pairing_codes")
    op.drop_table("bridge_devices")

    op.drop_constraint(op.f("ck_agent_invocations_status_valid"), "agent_invocations", type_="check")
    op.drop_column("agent_invocations", "queued_if_offline")
    op.drop_column("agent_invocations", "harness_session_ref")
    op.drop_column("agent_invocations", "delivered_at")
    op.create_check_constraint(
        op.f("ck_agent_invocations_status_valid"),
        "agent_invocations",
        "status IN ('queued', 'accepted', 'running', 'completed', 'failed', 'cancelled')",
    )

    op.drop_column("agent_sessions", "harness_session_ref")


def downgrade() -> None:
    raise NotImplementedError("The local bridge was removed. The dropped tables and columns cannot be recreated.")
