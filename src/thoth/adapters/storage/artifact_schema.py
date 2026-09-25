"""Artifact lineage and structural evidence table registration."""

from sqlalchemy import Column, ForeignKey, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_artifact_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table]:
    structural_nodes = Table(
        "structural_nodes",
        metadata,
        Column("node_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("artifact_id", String(160), nullable=False, index=True),
        Column("parent_id", String(160), nullable=True),
        Column("kind", String(60), nullable=True),
        Column("ordinal", Integer, nullable=True),
        Column("text", Text, nullable=True),
        Column("locator_json", Text, nullable=False),
        Column("source_version_id", String(160), nullable=True, index=True),
        Column("content_json", Text, nullable=True),
    )

    artifacts = Table(
        "artifacts",
        metadata,
        Column("artifact_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("source_uri", Text, nullable=False),
        Column("media_type", String(260), nullable=False),
        Column("byte_sha256", String(64), nullable=False),
        Column("authority", String(60), nullable=False),
        Column("cutoff_state", String(60), nullable=False),
        Column("security_class", String(60), nullable=False),
        Column("parser_name", String(160), nullable=False),
        Column("parser_version", String(80), nullable=False),
        Column("retrieved_at", String(32), nullable=False),
    )

    artifact_versions = Table(
        "artifact_versions",
        metadata,
        Column("source_version_id", String(160), primary_key=True),
        Column("artifact_id", String(160), ForeignKey("artifacts.artifact_id"), nullable=False),
        Column("version_label", String(260), nullable=True),
        Column("byte_sha256", String(64), nullable=False),
        Column("retrieved_at", String(32), nullable=False),
        Column("structure_metadata_json", Text, nullable=True),
    )

    evidence_spans = Table(
        "evidence_spans",
        metadata,
        Column("span_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("artifact_id", String(160), ForeignKey("artifacts.artifact_id"), nullable=False),
        Column(
            "source_version_id",
            String(160),
            ForeignKey("artifact_versions.source_version_id"),
            nullable=False,
        ),
        Column("locator_json", Text, nullable=False),
        Column("exact_text", Text, nullable=False),
        Column("text_sha256", String(64), nullable=False),
        Column("extraction_method", String(160), nullable=False),
        Column("support_state", String(60), nullable=False),
        Column("authority_state", String(60), nullable=False),
        Column("verification_state", String(60), nullable=False),
        Column("cutoff_state", String(60), nullable=False),
    )
    return structural_nodes, artifacts, artifact_versions, evidence_spans
