"""Decision object table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_decision_object_tables(
    metadata: MetaData,
) -> tuple[Table, Table, Table, Table, Table, Table]:
    object_profiles = Table(
        "object_profiles",
        metadata,
        Column("profile_ref", String(160), primary_key=True),
        Column("version", Integer, primary_key=True),
        Column("content_json", Text, nullable=False),
        Column("enabled", Integer, nullable=False),
        Column("profile_digest", String(64), nullable=False, unique=True),
    )

    object_candidates = Table(
        "object_candidates",
        metadata,
        Column("candidate_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("thread_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("candidate_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    decision_objects = Table(
        "decision_objects",
        metadata,
        Column("object_revision_id", String(160), primary_key=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("thread_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("supersedes_revision_digest", String(64), nullable=True),
        Column("created_at", String(32), nullable=False),
    )

    object_relations = Table(
        "object_relations",
        metadata,
        Column("relation_revision_id", String(160), primary_key=True),
        Column("relation_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("source_object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    object_attention = Table(
        "object_attention",
        metadata,
        Column("attention_revision_id", String(160), primary_key=True),
        Column("attention_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    object_audit = Table(
        "object_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return (
        object_profiles,
        object_candidates,
        decision_objects,
        object_relations,
        object_attention,
        object_audit,
    )
