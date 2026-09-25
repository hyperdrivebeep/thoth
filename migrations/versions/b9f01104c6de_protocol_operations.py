"""protocol operations and project overlay

Revision ID: b9f01104c6de
Revises: e154fa1aa4eb
Create Date: 2026-08-30 06:34:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9f01104c6de"
down_revision: str | None = "e154fa1aa4eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column("overlay", sa.String(length=160), nullable=False, server_default="default")
        )

    op.create_table(
        "operations",
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("method", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=260), nullable=False),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.String(length=32), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint(
            "project_id", "method", "idempotency_key", name="uq_operation_idempotency_scope"
        ),
    )
    op.create_index("ix_operations_project_id", "operations", ["project_id"])
    op.create_table(
        "idempotency_keys",
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("method", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=260), nullable=False),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.operation_id"]),
        sa.PrimaryKeyConstraint("project_id", "method", "idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_operations_project_id", table_name="operations")
    op.drop_table("operations")
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("overlay")
