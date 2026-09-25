"""A06 typed full project memory lifecycle

Revision ID: 7b06e5a9d3f1
Revises: 6a10d4f9c2e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b06e5a9d3f1"
down_revision: str | None = "6a10d4f9c2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_revision_ledger",
        sa.Column("memory_revision_id", sa.String(200), primary_key=True),
        sa.Column("memory_id", sa.String(160), nullable=False),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("transition", sa.String(40), nullable=False),
        sa.Column("owner_revision_ref", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("revision_digest", sa.String(64), nullable=False, unique=True),
        sa.UniqueConstraint(
            "project_id", "memory_id", name="uq_memory_revision_source_memory"
        ),
    )
    op.create_index(
        "ix_memory_revision_ledger_project_id", "memory_revision_ledger", ["project_id"]
    )
    op.create_table(
        "memory_transition_receipts",
        sa.Column("receipt_id", sa.String(200), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("memory_revision_id", sa.String(200), nullable=False, unique=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False, unique=True),
    )
    op.create_index(
        "ix_memory_transition_receipts_project_id",
        "memory_transition_receipts",
        ["project_id"],
    )
    op.create_table(
        "memory_context_packs",
        sa.Column("context_pack_id", sa.String(200), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("thread_id", sa.String(160), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("query_digest", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_memory_context_packs_project_id", "memory_context_packs", ["project_id"]
    )
    op.create_table(
        "memory_projections",
        sa.Column("projection_id", sa.String(200), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("projection_type", sa.String(40), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("rebuild_checkpoint", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "project_id", "projection_type", name="uq_memory_projection_type"
        ),
    )
    op.create_index(
        "ix_memory_projections_project_id", "memory_projections", ["project_id"]
    )


def downgrade() -> None:
    for name in (
        "memory_projections",
        "memory_context_packs",
        "memory_transition_receipts",
        "memory_revision_ledger",
    ):
        op.drop_index(f"ix_{name}_project_id", table_name=name)
        op.drop_table(name)
