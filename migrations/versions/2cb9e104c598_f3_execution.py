"""F3 durable plan execution attempts effects reconciliation and audit

Revision ID: 2cb9e104c598
Revises: 1ba8d0f3b487
Create Date: 2026-08-31 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2cb9e104c598"
down_revision: str | None = "1ba8d0f3b487"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table(name: str, primary: str, indexed: tuple[str, ...], digest: str) -> None:
    columns: list[sa.Column[object]] = [sa.Column(primary, sa.String(160), primary_key=True)]
    columns.extend(sa.Column(column, sa.String(160), nullable=False) for column in indexed)
    columns.extend(
        (
            sa.Column("content_json", sa.Text(), nullable=False),
            sa.Column(digest, sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.String(32), nullable=False),
        )
    )
    op.create_table(name, *columns)
    for column in indexed:
        op.create_index(f"ix_{name}_{column}", name, [column])


def upgrade() -> None:
    _table(
        "plan_executions",
        "execution_revision_id",
        ("plan_execution_id", "project_id", "object_id", "plan_id"),
        "revision_digest",
    )
    _table(
        "step_execution_attempts",
        "attempt_revision_id",
        ("attempt_id", "project_id", "plan_execution_id", "step_id"),
        "revision_digest",
    )
    _table(
        "execution_effects",
        "effect_id",
        ("project_id", "attempt_id"),
        "effect_digest",
    )
    _table(
        "execution_reconciliations",
        "reconciliation_id",
        ("project_id", "plan_execution_id", "attempt_id"),
        "reconciliation_digest",
    )
    op.create_table(
        "execution_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("plan_execution_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_execution_audit_project_id", "execution_audit", ["project_id"])
    op.create_index(
        "ix_execution_audit_plan_execution_id",
        "execution_audit",
        ["plan_execution_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_audit_plan_execution_id", table_name="execution_audit")
    op.drop_index("ix_execution_audit_project_id", table_name="execution_audit")
    op.drop_table("execution_audit")
    definitions = (
        (
            "execution_reconciliations",
            ("project_id", "plan_execution_id", "attempt_id"),
        ),
        ("execution_effects", ("project_id", "attempt_id")),
        (
            "step_execution_attempts",
            ("attempt_id", "project_id", "plan_execution_id", "step_id"),
        ),
        (
            "plan_executions",
            ("plan_execution_id", "project_id", "object_id", "plan_id"),
        ),
    )
    for table, columns in definitions:
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
