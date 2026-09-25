"""F2 evidence source observation link conflict and audit

Revision ID: d64a9ebf7043
Revises: c53f8dae6f42
Create Date: 2026-08-31 02:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d64a9ebf7043"
down_revision: str | None = "c53f8dae6f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_sources",
        sa.Column("source_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("artifact_id", sa.String(160), nullable=False),
        sa.Column("connector_ref", sa.String(160), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.String(160), nullable=False),
        sa.Column("version", sa.String(260), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("authority_status", sa.String(60), nullable=False),
        sa.Column("security_class", sa.String(60), nullable=False),
        sa.Column("official_copy", sa.Integer(), nullable=False),
        sa.Column("rights", sa.String(160), nullable=False),
        sa.Column("retention", sa.String(160), nullable=False),
        sa.Column("valid_time", sa.String(32), nullable=True),
        sa.Column("snapshot_time", sa.String(32), nullable=True),
        sa.Column("cutoff_eligibility", sa.String(60), nullable=False),
        sa.Column("lineage_root_id", sa.String(160), nullable=False),
        sa.Column("parent_source_ids_json", sa.Text(), nullable=False),
        sa.Column("supersedes_source_id", sa.String(160), nullable=True),
        sa.Column("source_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_evidence_sources_project_id", "evidence_sources", ["project_id"])
    op.create_index("ix_evidence_sources_artifact_id", "evidence_sources", ["artifact_id"])
    op.create_table(
        "evidence_observations",
        sa.Column("observation_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("span_ids_json", sa.Text(), nullable=False),
        sa.Column("observed_statement", sa.Text(), nullable=False),
        sa.Column("observed_time", sa.String(32), nullable=True),
        sa.Column("valid_time", sa.String(32), nullable=True),
        sa.Column("observer_group", sa.String(160), nullable=False),
        sa.Column("independence_basis", sa.Text(), nullable=False),
        sa.Column("provenance_class", sa.String(80), nullable=False),
        sa.Column("contamination_note", sa.Text(), nullable=True),
        sa.Column("supersedes_observation_id", sa.String(160), nullable=True),
        sa.Column("observation_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_evidence_observations_project_id", "evidence_observations", ["project_id"])
    op.create_table(
        "evidence_links",
        sa.Column("evidence_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("thread_id", sa.String(160), nullable=True),
        sa.Column("target_type", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=False),
        sa.Column("relation", sa.String(40), nullable=False),
        sa.Column("source_ids_json", sa.Text(), nullable=False),
        sa.Column("span_ids_json", sa.Text(), nullable=False),
        sa.Column("observation_ids_json", sa.Text(), nullable=False),
        sa.Column("conditions_json", sa.Text(), nullable=False),
        sa.Column("applicability", sa.Text(), nullable=False),
        sa.Column("independence_group", sa.String(160), nullable=False),
        sa.Column("support_status", sa.String(60), nullable=False),
        sa.Column("authority_status", sa.String(60), nullable=False),
        sa.Column("verification_status", sa.String(60), nullable=False),
        sa.Column("cutoff_eligibility", sa.String(60), nullable=False),
        sa.Column("content_trust", sa.String(60), nullable=False),
        sa.Column("conflict_ids_json", sa.Text(), nullable=False),
        sa.Column("supersedes_evidence_id", sa.String(160), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_evidence_links_project_id", "evidence_links", ["project_id"])
    op.create_index("ix_evidence_links_target_id", "evidence_links", ["target_id"])
    op.create_table(
        "evidence_conflicts",
        sa.Column("conflict_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("field", sa.String(160), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resolution_evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("conflict_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("conflict_id"),
    )
    op.create_index("ix_evidence_conflicts_project_id", "evidence_conflicts", ["project_id"])
    op.create_table(
        "evidence_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("evidence_ref", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_evidence_audit_project_id", "evidence_audit", ["project_id"])
    op.create_index("ix_evidence_audit_evidence_ref", "evidence_audit", ["evidence_ref"])
    op.create_table(
        "span_corrections",
        sa.Column("correction_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("span_id", sa.String(160), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correction_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_span_corrections_project_id", "span_corrections", ["project_id"])
    op.create_index("ix_span_corrections_span_id", "span_corrections", ["span_id"])


def downgrade() -> None:
    for table, indexes in (
        ("span_corrections", ("ix_span_corrections_span_id", "ix_span_corrections_project_id")),
        ("evidence_audit", ("ix_evidence_audit_evidence_ref", "ix_evidence_audit_project_id")),
        ("evidence_conflicts", ("ix_evidence_conflicts_project_id",)),
        ("evidence_links", ("ix_evidence_links_target_id", "ix_evidence_links_project_id")),
        ("evidence_observations", ("ix_evidence_observations_project_id",)),
        ("evidence_sources", ("ix_evidence_sources_artifact_id", "ix_evidence_sources_project_id")),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
