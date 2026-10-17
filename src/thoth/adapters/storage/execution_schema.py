"""Execution table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import String, Text


def define_execution_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table]:
    plan_executions = Table(
        "plan_executions",
        metadata,
        Column("execution_revision_id", String(160), primary_key=True),
        Column("plan_execution_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("plan_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    step_execution_attempts = Table(
        "step_execution_attempts",
        metadata,
        Column("attempt_revision_id", String(160), primary_key=True),
        Column("attempt_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("plan_execution_id", String(160), nullable=False, index=True),
        Column("step_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    execution_effects = Table(
        "execution_effects",
        metadata,
        Column("effect_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("attempt_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("effect_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    execution_reconciliations = Table(
        "execution_reconciliations",
        metadata,
        Column("reconciliation_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("plan_execution_id", String(160), nullable=False, index=True),
        Column("attempt_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("reconciliation_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    execution_audit = Table(
        "execution_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("plan_execution_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return (
        plan_executions,
        step_execution_attempts,
        execution_effects,
        execution_reconciliations,
        execution_audit,
    )
