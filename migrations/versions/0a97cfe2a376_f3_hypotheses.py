"""F3 hypothesis records portfolios predictions tests and appraisal

Revision ID: 0a97cfe2a376
Revises: f86cbed19265
Create Date: 2026-08-31 07:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a97cfe2a376"
down_revision: str | None = "f86cbed19265"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _record_table(
    name: str,
    primary: str,
    indexed: tuple[str, ...],
    digest: str,
) -> None:
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
    _record_table(
        "hypothesis_records",
        "hypothesis_revision_id",
        ("hypothesis_id", "project_id", "object_id", "portfolio_id"),
        "revision_digest",
    )
    _record_table(
        "hypothesis_portfolios",
        "portfolio_revision_id",
        ("portfolio_id", "project_id", "object_id"),
        "revision_digest",
    )
    _record_table(
        "hypothesis_relations",
        "relation_revision_id",
        ("relation_id", "project_id", "portfolio_id"),
        "revision_digest",
    )
    _record_table(
        "hypothesis_assumptions",
        "assumption_id",
        ("project_id", "hypothesis_id"),
        "assumption_digest",
    )
    _record_table(
        "hypothesis_predictions",
        "prediction_id",
        ("project_id", "hypothesis_id"),
        "prediction_digest",
    )
    _record_table(
        "hypothesis_test_bindings",
        "test_binding_id",
        ("project_id", "prediction_id"),
        "binding_digest",
    )
    _record_table(
        "hypothesis_appraisals",
        "appraisal_id",
        ("project_id", "hypothesis_id"),
        "appraisal_digest",
    )
    op.create_table(
        "hypothesis_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("hypothesis_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_hypothesis_audit_project_id", "hypothesis_audit", ["project_id"])
    op.create_index("ix_hypothesis_audit_hypothesis_id", "hypothesis_audit", ["hypothesis_id"])


def downgrade() -> None:
    op.drop_index("ix_hypothesis_audit_hypothesis_id", table_name="hypothesis_audit")
    op.drop_index("ix_hypothesis_audit_project_id", table_name="hypothesis_audit")
    op.drop_table("hypothesis_audit")
    definitions = (
        ("hypothesis_appraisals", ("project_id", "hypothesis_id")),
        ("hypothesis_test_bindings", ("project_id", "prediction_id")),
        ("hypothesis_predictions", ("project_id", "hypothesis_id")),
        ("hypothesis_assumptions", ("project_id", "hypothesis_id")),
        ("hypothesis_relations", ("relation_id", "project_id", "portfolio_id")),
        ("hypothesis_portfolios", ("portfolio_id", "project_id", "object_id")),
        (
            "hypothesis_records",
            ("hypothesis_id", "project_id", "object_id", "portfolio_id"),
        ),
    )
    for table, columns in definitions:
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
