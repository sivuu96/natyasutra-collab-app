"""agent repository

Revision ID: c5e6f7a8b9c0
Revises: b4d5e6f7a8b9
Create Date: 2026-11-11 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "b4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("repository_full_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("default_ref", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("default_ref")
        batch.drop_column("repository_full_name")
