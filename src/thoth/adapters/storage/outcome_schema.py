"""Outcome table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_outcome_tables(
    metadata: MetaData,
) -> tuple[Table, Table, Table, Table, Table, Table, Table]:
    outcome_profiles = Table(
        "outcome_profiles",
        metadata,
        Column("profile_ref", String(160), primary_key=True),
        Column("version", Integer, primary_key=True),
        Column("content_json", Text, nullable=False),
        Column("enabled", Integer, nullable=False),
        Column("profile_digest", String(64), nullable=False, unique=True),
    )

    outcome_series = Table(
        "outcome_series",
        metadata,
        Column("series_revision_id", String(160), primary_key=True),
        Column("outcome_series_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    outcome_assessments = Table(
        "outcome_assessments",
        metadata,
        Column("assessment_revision_id", String(160), primary_key=True),
        Column("outcome_assessment_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("outcome_series_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    outcome_attributions = Table(
        "outcome_attributions",
        metadata,
        Column("attribution_assessment_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("outcome_assessment_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("attribution_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    outcome_change_sets = Table(
        "outcome_change_sets",
        metadata,
        Column("outcome_change_set_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("outcome_assessment_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("change_set_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    outcome_impacts = Table(
        "outcome_impacts",
        metadata,
        Column("impact_assessment_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("outcome_series_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("impact_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    outcome_audit = Table(
        "outcome_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("subject_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return (
        outcome_profiles,
        outcome_series,
        outcome_assessments,
        outcome_attributions,
        outcome_change_sets,
        outcome_impacts,
        outcome_audit,
    )
