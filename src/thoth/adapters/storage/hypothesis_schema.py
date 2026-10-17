"""Hypothesis table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import String, Text


def define_hypothesis_tables(
    metadata: MetaData,
) -> tuple[Table, Table, Table, Table, Table, Table, Table, Table]:
    hypothesis_records = Table(
        "hypothesis_records",
        metadata,
        Column("hypothesis_revision_id", String(160), primary_key=True),
        Column("hypothesis_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("portfolio_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_portfolios = Table(
        "hypothesis_portfolios",
        metadata,
        Column("portfolio_revision_id", String(160), primary_key=True),
        Column("portfolio_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_relations = Table(
        "hypothesis_relations",
        metadata,
        Column("relation_revision_id", String(160), primary_key=True),
        Column("relation_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("portfolio_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_assumptions = Table(
        "hypothesis_assumptions",
        metadata,
        Column("assumption_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("hypothesis_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("assumption_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_predictions = Table(
        "hypothesis_predictions",
        metadata,
        Column("prediction_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("hypothesis_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("prediction_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_test_bindings = Table(
        "hypothesis_test_bindings",
        metadata,
        Column("test_binding_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("prediction_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("binding_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_appraisals = Table(
        "hypothesis_appraisals",
        metadata,
        Column("appraisal_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("hypothesis_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("appraisal_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    hypothesis_audit = Table(
        "hypothesis_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("hypothesis_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return (
        hypothesis_records,
        hypothesis_portfolios,
        hypothesis_relations,
        hypothesis_assumptions,
        hypothesis_predictions,
        hypothesis_test_bindings,
        hypothesis_appraisals,
        hypothesis_audit,
    )
