"""Action table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import String, Text


def define_action_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table]:
    action_records = Table(
        "action_records",
        metadata,
        Column("action_revision_id", String(160), primary_key=True),
        Column("action_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("portfolio_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    action_portfolios = Table(
        "action_portfolios",
        metadata,
        Column("portfolio_revision_id", String(160), primary_key=True),
        Column("portfolio_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    action_plans = Table(
        "action_plans",
        metadata,
        Column("plan_revision_id", String(160), primary_key=True),
        Column("plan_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("object_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    action_authorizations = Table(
        "action_authorizations",
        metadata,
        Column("authorization_revision_id", String(160), primary_key=True),
        Column("authorization_id", String(160), nullable=False, index=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("plan_id", String(160), nullable=False, index=True),
        Column("step_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("revision_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    action_audit = Table(
        "action_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("subject_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return action_records, action_portfolios, action_plans, action_authorizations, action_audit
