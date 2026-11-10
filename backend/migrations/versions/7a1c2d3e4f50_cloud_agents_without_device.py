"""cloud agents: device_id is optional

Revision ID: 7a1c2d3e4f50
Revises: 5e0d1f2a3b4c
Create Date: 2026-11-10 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1c2d3e4f50"
down_revision: str | Sequence[str] | None = "5e0d1f2a3b4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.alter_column("device_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    # Fails if any cloud agent (device_id NULL) exists. Remove those agents before downgrading.
    with op.batch_alter_table("agents") as batch:
        batch.alter_column("device_id", existing_type=sa.Uuid(), nullable=False)
