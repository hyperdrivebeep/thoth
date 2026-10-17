"""Evidence table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_evidence_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table, Table]:
    evidence_sources = Table(
        "evidence_sources",
        metadata,
        Column("source_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("artifact_id", String(160), nullable=False, index=True),
        Column("connector_ref", String(160), nullable=False),
        Column("uri", Text, nullable=False),
        Column("artifact_type", String(160), nullable=False),
        Column("version", String(260), nullable=True),
        Column("sha256", String(64), nullable=False),
        Column("authority_status", String(60), nullable=False),
        Column("security_class", String(60), nullable=False),
        Column("official_copy", Integer, nullable=False),
        Column("rights", String(160), nullable=False),
        Column("retention", String(160), nullable=False),
        Column("valid_time", String(32), nullable=True),
        Column("snapshot_time", String(32), nullable=True),
        Column("cutoff_eligibility", String(60), nullable=False),
        Column("lineage_root_id", String(160), nullable=False),
        Column("parent_source_ids_json", Text, nullable=False),
        Column("supersedes_source_id", String(160), nullable=True),
        Column("source_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    evidence_observations = Table(
        "evidence_observations",
        metadata,
        Column("observation_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("span_ids_json", Text, nullable=False),
        Column("observed_statement", Text, nullable=False),
        Column("observed_time", String(32), nullable=True),
        Column("valid_time", String(32), nullable=True),
        Column("observer_group", String(160), nullable=False),
        Column("independence_basis", Text, nullable=False),
        Column("provenance_class", String(80), nullable=False),
        Column("contamination_note", Text, nullable=True),
        Column("supersedes_observation_id", String(160), nullable=True),
        Column("observation_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    evidence_links = Table(
        "evidence_links",
        metadata,
        Column("evidence_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("thread_id", String(160), nullable=True),
        Column("target_type", String(80), nullable=False),
        Column("target_id", String(160), nullable=False, index=True),
        Column("relation", String(40), nullable=False),
        Column("source_ids_json", Text, nullable=False),
        Column("span_ids_json", Text, nullable=False),
        Column("observation_ids_json", Text, nullable=False),
        Column("conditions_json", Text, nullable=False),
        Column("applicability", Text, nullable=False),
        Column("independence_group", String(160), nullable=False),
        Column("support_status", String(60), nullable=False),
        Column("authority_status", String(60), nullable=False),
        Column("verification_status", String(60), nullable=False),
        Column("cutoff_eligibility", String(60), nullable=False),
        Column("content_trust", String(60), nullable=False),
        Column("conflict_ids_json", Text, nullable=False),
        Column("supersedes_evidence_id", String(160), nullable=True),
        Column("revision", Integer, nullable=False),
        Column("evidence_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    evidence_conflicts = Table(
        "evidence_conflicts",
        metadata,
        Column("conflict_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("evidence_ids_json", Text, nullable=False),
        Column("field", String(160), nullable=False),
        Column("status", String(40), nullable=False),
        Column("reason", Text, nullable=False),
        Column("resolution_evidence_ids_json", Text, nullable=False),
        Column("conflict_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
        Column("updated_at", String(32), nullable=False),
    )

    evidence_audit = Table(
        "evidence_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("evidence_ref", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    span_corrections = Table(
        "span_corrections",
        metadata,
        Column("correction_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("span_id", String(160), nullable=False, index=True),
        Column("corrected_text", Text, nullable=False),
        Column("reason", Text, nullable=False),
        Column("correction_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return (
        evidence_sources,
        evidence_observations,
        evidence_links,
        evidence_conflicts,
        evidence_audit,
        span_corrections,
    )
