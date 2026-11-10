"""user handles

Revision ID: e7a8b9c0d1e2
Revises: d6f7a8b9c0d1
Create Date: 2026-11-11 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "d6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HANDLE_MAX = 30


def _base(email: str) -> str:
    import re

    local = email.split("@", 1)[0].lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", local).strip("-_")[:HANDLE_MAX].strip("-_")
    if len(cleaned) < 2 or not cleaned[0].isalnum():
        cleaned = f"user{cleaned}".strip("-_")[:HANDLE_MAX]
    return cleaned


def upgrade() -> None:
    op.add_column("users", sa.Column("handle", sa.String(length=40), nullable=True))
    op.create_unique_constraint("uq_users_handle", "users", ["handle"])

    bind = op.get_bind()
    taken = {row[0] for row in bind.execute(sa.text("SELECT handle FROM agents"))}
    users = bind.execute(sa.text("SELECT id, email FROM users ORDER BY created_at, id")).fetchall()
    for user_id, email in users:
        base = _base(email)
        candidate, number = base, 2
        while candidate in taken:
            suffix = f"-{number}"
            candidate = base[: HANDLE_MAX - len(suffix)] + suffix
            number += 1
        taken.add(candidate)
        bind.execute(sa.text("UPDATE users SET handle = :h WHERE id = :id"), {"h": candidate, "id": user_id})


def downgrade() -> None:
    op.drop_constraint("uq_users_handle", "users", type_="unique")
    op.drop_column("users", "handle")
