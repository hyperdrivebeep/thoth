"""project work threads

Revision ID: e311a930d5f4
Revises: d4ac821bf1e2
Create Date: 2026-08-30 07:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e311a930d5f4"
down_revision: str | None = "d4ac821bf1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("thread_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("cycle_id", sa.String(length=160), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("lifecycle", sa.String(length=60), nullable=False),
        sa.Column("execution_state", sa.String(length=60), nullable=False),
        sa.Column("current_object_ids_json", sa.Text(), nullable=False),
        sa.Column("working_head_digest", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index("ix_threads_project_id", "threads", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_project_id", table_name="threads")
    op.drop_table("threads")
