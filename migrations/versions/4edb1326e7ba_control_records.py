"""Shared append-only control records for revision memory improvement and lifecycle

Revision ID: 4edb1326e7ba
Revises: 3dca0215d6a9
Create Date: 2026-08-31 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4edb1326e7ba"
down_revision: str | None = "3dca0215d6a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "control_records",
        sa.Column("control_revision_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("namespace", sa.String(80), nullable=False),
        sa.Column("record_type", sa.String(120), nullable=False),
        sa.Column("record_id", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(80), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("supersedes_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    for column in ("project_id", "namespace", "record_type", "record_id", "state"):
        op.create_index(f"ix_control_records_{column}", "control_records", [column])


def downgrade() -> None:
    for column in ("state", "record_id", "record_type", "namespace", "project_id"):
        op.drop_index(f"ix_control_records_{column}", table_name="control_records")
    op.drop_table("control_records")
