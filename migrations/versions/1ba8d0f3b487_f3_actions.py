"""F3 action records portfolios plans authorization and audit

Revision ID: 1ba8d0f3b487
Revises: 0a97cfe2a376
Create Date: 2026-08-31 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1ba8d0f3b487"
down_revision: str | None = "0a97cfe2a376"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table(
    name: str,
    primary: str,
    indexed: tuple[str, ...],
) -> None:
    columns: list[sa.Column[object]] = [sa.Column(primary, sa.String(160), primary_key=True)]
    columns.extend(sa.Column(column, sa.String(160), nullable=False) for column in indexed)
    columns.extend(
        (
            sa.Column("content_json", sa.Text(), nullable=False),
            sa.Column("revision_digest", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.String(32), nullable=False),
        )
    )
    op.create_table(name, *columns)
    for column in indexed:
        op.create_index(f"ix_{name}_{column}", name, [column])


def upgrade() -> None:
    _table(
        "action_records",
        "action_revision_id",
        ("action_id", "project_id", "object_id", "portfolio_id"),
    )
    _table(
        "action_portfolios",
        "portfolio_revision_id",
        ("portfolio_id", "project_id", "object_id"),
    )
    _table(
        "action_plans",
        "plan_revision_id",
        ("plan_id", "project_id", "object_id"),
    )
    _table(
        "action_authorizations",
        "authorization_revision_id",
        ("authorization_id", "project_id", "plan_id", "step_id"),
    )
    op.create_table(
        "action_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_action_audit_project_id", "action_audit", ["project_id"])
    op.create_index("ix_action_audit_subject_id", "action_audit", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_action_audit_subject_id", table_name="action_audit")
    op.drop_index("ix_action_audit_project_id", table_name="action_audit")
    op.drop_table("action_audit")
    definitions = (
        (
            "action_authorizations",
            ("authorization_id", "project_id", "plan_id", "step_id"),
        ),
        ("action_plans", ("plan_id", "project_id", "object_id")),
        ("action_portfolios", ("portfolio_id", "project_id", "object_id")),
        (
            "action_records",
            ("action_id", "project_id", "object_id", "portfolio_id"),
        ),
    )
    for table, columns in definitions:
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
