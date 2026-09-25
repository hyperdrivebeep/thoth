"""Persist authenticated operation ownership for cancellation authorization.

Revision ID: f7a69b2345cd
Revises: e6f58a1234bc
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a69b2345cd"
down_revision: str | None = "e6f58a1234bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("owner_actor_id", sa.String(160), nullable=True))
    op.add_column("operations", sa.Column("owner_session_id", sa.String(160), nullable=True))
    op.add_column(
        "operations",
        sa.Column("owner_role_assignment_id", sa.String(160), nullable=True),
    )
    op.add_column(
        "operations",
        sa.Column("owner_data_scopes_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("owner_data_scopes_json")
        batch.drop_column("owner_role_assignment_id")
        batch.drop_column("owner_session_id")
        batch.drop_column("owner_actor_id")
