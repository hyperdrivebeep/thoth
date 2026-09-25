"""F2 criterion profiles contracts references conflicts and audit

Revision ID: e75badc08154
Revises: d64a9ebf7043
Create Date: 2026-08-31 03:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e75badc08154"
down_revision: str | None = "d64a9ebf7043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "criterion_profiles",
        sa.Column("profile_ref", sa.String(160), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain_hint", sa.String(160), nullable=False),
        sa.Column("required_fields_json", sa.Text(), nullable=False),
        sa.Column("conditional_fields_json", sa.Text(), nullable=False),
        sa.Column("allowed_computation_types_json", sa.Text(), nullable=False),
        sa.Column("authority_policy_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("profile_digest", sa.String(64), nullable=False, unique=True),
    )
    op.create_table(
        "criterion_contracts",
        sa.Column("criterion_revision_id", sa.String(160), primary_key=True),
        sa.Column("criterion_id", sa.String(160), nullable=False),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("thread_id", sa.String(160), nullable=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("revision_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("supersedes_revision_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_criterion_contracts_criterion_id", "criterion_contracts", ["criterion_id"])
    op.create_index("ix_criterion_contracts_project_id", "criterion_contracts", ["project_id"])
    op.create_table(
        "criterion_references",
        sa.Column("reference_candidate_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("criterion_id", sa.String(160), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("reference_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_criterion_references_project_id", "criterion_references", ["project_id"])
    op.create_index(
        "ix_criterion_references_criterion_id", "criterion_references", ["criterion_id"]
    )
    op.create_table(
        "criterion_conflicts",
        sa.Column("conflict_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("criterion_id", sa.String(160), nullable=False),
        sa.Column("field_path", sa.String(260), nullable=False),
        sa.Column("candidate_values_json", sa.Text(), nullable=False),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("impact_json", sa.Text(), nullable=False),
        sa.Column("conflict_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_criterion_conflicts_project_id", "criterion_conflicts", ["project_id"])
    op.create_index("ix_criterion_conflicts_criterion_id", "criterion_conflicts", ["criterion_id"])
    op.create_table(
        "criterion_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("criterion_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_criterion_audit_project_id", "criterion_audit", ["project_id"])
    op.create_index("ix_criterion_audit_criterion_id", "criterion_audit", ["criterion_id"])


def downgrade() -> None:
    for table, indexes in (
        (
            "criterion_audit",
            ("ix_criterion_audit_criterion_id", "ix_criterion_audit_project_id"),
        ),
        (
            "criterion_conflicts",
            ("ix_criterion_conflicts_criterion_id", "ix_criterion_conflicts_project_id"),
        ),
        (
            "criterion_references",
            ("ix_criterion_references_criterion_id", "ix_criterion_references_project_id"),
        ),
        (
            "criterion_contracts",
            ("ix_criterion_contracts_project_id", "ix_criterion_contracts_criterion_id"),
        ),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
    op.drop_table("criterion_profiles")
