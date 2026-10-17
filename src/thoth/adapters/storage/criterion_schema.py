"""Criterion table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_criterion_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table]:
    criterion_profiles = Table(
        "criterion_profiles",
        metadata,
        Column("profile_ref", String(160), primary_key=True),
        Column("version", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("domain_hint", String(160), nullable=False),
        Column("required_fields_json", Text, nullable=False),
        Column("conditional_fields_json", Text, nullable=False),
        Column("allowed_computation_types_json", Text, nullable=False),
        Column("authority_policy_json", Text, nullable=False),
        Column("enabled", Integer, nullable=False),
        Column("profile_digest", String(64), nullable=False, unique=True),
        Column("profile_json", Text, nullable=True),
    )

    criterion_contracts = Table(
        "criterion_contracts",
        metadata,
        Column("criterion_revision_id", String(160), primary_key=True),
        Column("criterion_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("thread_id", String(160), nullable=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("supersedes_revision_digest", String(64), nullable=True),
        Column("created_at", String(32), nullable=False),
    )

    criterion_references = Table(
        "criterion_references",
        metadata,
        Column("reference_candidate_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("criterion_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("reference_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    criterion_conflicts = Table(
        "criterion_conflicts",
        metadata,
        Column("conflict_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("criterion_id", String(160), nullable=False, index=True),
        Column("field_path", String(260), nullable=False),
        Column("candidate_values_json", Text, nullable=False),
        Column("evidence_refs_json", Text, nullable=False),
        Column("status", String(40), nullable=False),
        Column("impact_json", Text, nullable=False),
        Column("conflict_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    criterion_audit = Table(
        "criterion_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("criterion_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return (
        criterion_profiles,
        criterion_contracts,
        criterion_references,
        criterion_conflicts,
        criterion_audit,
    )
