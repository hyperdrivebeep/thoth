"""P8 noncanonical TUI session projection

Revision ID: d5e47f9013ab
Revises: c4d36e8f102a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e47f9013ab"
down_revision: str | None = "c4d36e8f102a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tui_sessions",
        sa.Column("session_id", sa.String(200), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tui_sessions")
