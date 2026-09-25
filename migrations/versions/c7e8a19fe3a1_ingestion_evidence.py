"""ingestion evidence ledger

Revision ID: c7e8a19fe3a1
Revises: b9f01104c6de
Create Date: 2026-08-30 06:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e8a19fe3a1"
down_revision: str | None = "b9f01104c6de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=260), nullable=False),
        sa.Column("byte_sha256", sa.String(length=64), nullable=False),
        sa.Column("authority", sa.String(length=60), nullable=False),
        sa.Column("cutoff_state", sa.String(length=60), nullable=False),
        sa.Column("security_class", sa.String(length=60), nullable=False),
        sa.Column("parser_name", sa.String(length=160), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("retrieved_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_table(
        "artifact_versions",
        sa.Column("source_version_id", sa.String(length=160), nullable=False),
        sa.Column("artifact_id", sa.String(length=160), nullable=False),
        sa.Column("version_label", sa.String(length=260), nullable=True),
        sa.Column("byte_sha256", sa.String(length=64), nullable=False),
        sa.Column("retrieved_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.artifact_id"]),
        sa.PrimaryKeyConstraint("source_version_id"),
    )
    op.create_table(
        "evidence_spans",
        sa.Column("span_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("artifact_id", sa.String(length=160), nullable=False),
        sa.Column("source_version_id", sa.String(length=160), nullable=False),
        sa.Column("locator_json", sa.Text(), nullable=False),
        sa.Column("exact_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("extraction_method", sa.String(length=160), nullable=False),
        sa.Column("support_state", sa.String(length=60), nullable=False),
        sa.Column("authority_state", sa.String(length=60), nullable=False),
        sa.Column("verification_state", sa.String(length=60), nullable=False),
        sa.Column("cutoff_state", sa.String(length=60), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.artifact_id"]),
        sa.ForeignKeyConstraint(["source_version_id"], ["artifact_versions.source_version_id"]),
        sa.PrimaryKeyConstraint("span_id"),
    )
    op.create_index("ix_evidence_spans_project_id", "evidence_spans", ["project_id"])
    with op.batch_alter_table("structural_nodes") as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("kind", sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column("ordinal", sa.Integer(), nullable=True))
        batch_op.alter_column("text", existing_type=sa.Text(), nullable=True)
    op.execute("DROP TABLE IF EXISTS structural_fts")
    op.execute(
        "CREATE VIRTUAL TABLE structural_fts USING fts5("
        "node_id UNINDEXED, project_id UNINDEXED, artifact_id UNINDEXED, "
        "text, tokenize='trigram')"
    )
    op.execute(
        "INSERT INTO structural_fts(node_id, project_id, artifact_id, text) "
        "SELECT node_id, project_id, artifact_id, text FROM structural_nodes WHERE text IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS structural_fts")
    op.execute(
        "CREATE VIRTUAL TABLE structural_fts USING fts5("
        "node_id UNINDEXED, project_id UNINDEXED, artifact_id UNINDEXED, text)"
    )
    op.execute(
        "INSERT INTO structural_fts(node_id, project_id, artifact_id, text) "
        "SELECT node_id, project_id, artifact_id, text FROM structural_nodes WHERE text IS NOT NULL"
    )
    with op.batch_alter_table("structural_nodes") as batch_op:
        batch_op.alter_column("text", existing_type=sa.Text(), nullable=False)
        batch_op.drop_column("ordinal")
        batch_op.drop_column("kind")
        batch_op.drop_column("parent_id")
    op.drop_index("ix_evidence_spans_project_id", table_name="evidence_spans")
    op.drop_table("evidence_spans")
    op.drop_table("artifact_versions")
    op.drop_index("ix_artifacts_project_id", table_name="artifacts")
    op.drop_table("artifacts")
