"""Investigation table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_investigation_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table]:
    investigations = Table(
        "investigations",
        metadata,
        Column("investigation_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("thread_id", String(160), nullable=False, index=True),
        Column("cycle_id", String(160), nullable=False),
        Column("parent_investigation_id", String(160), nullable=True),
        Column("trigger", String(80), nullable=False),
        Column("question", Text, nullable=False),
        Column("target_object_id", String(160), nullable=True),
        Column("target_hypothesis_id", String(160), nullable=True),
        Column("mode", String(40), nullable=False),
        Column("scope_json", Text, nullable=False),
        Column("required_evidence_groups_json", Text, nullable=False),
        Column("query_families_json", Text, nullable=False),
        Column("counter_search_policy", String(160), nullable=False),
        Column("budget", Integer, nullable=False),
        Column("budget_usage", Integer, nullable=False),
        Column("stop_conditions_json", Text, nullable=False),
        Column("domain_state", String(40), nullable=False),
        Column("execution_state", String(40), nullable=False),
        Column("current_wave", Integer, nullable=False),
        Column("observation_count", Integer, nullable=False),
        Column("open_lead_count", Integer, nullable=False),
        Column("claim_candidate_count", Integer, nullable=False),
        Column("gap_count", Integer, nullable=False),
        Column("sufficiency_json", Text, nullable=False),
        Column("checkpoint_digest", String(64), nullable=True),
        Column("result_json", Text, nullable=True),
        Column("plan_revision", Integer, nullable=False),
        Column("investigation_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
        Column("updated_at", String(32), nullable=False),
    )

    investigation_audit = Table(
        "investigation_audit",
        metadata,
        Column("audit_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("investigation_id", String(160), nullable=False, index=True),
        Column("event_type", String(120), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("event_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    acquisition_search_intents = Table(
        "acquisition_search_intents",
        metadata,
        Column("search_intent_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("investigation_id", String(160), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("intent_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )

    evidence_leads = Table(
        "evidence_leads",
        metadata,
        Column("lead_id", String(160), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("investigation_id", String(160), nullable=False, index=True),
        Column("search_intent_id", String(160), nullable=False, index=True),
        Column("source_id", String(160), nullable=False, index=True),
        Column("span_id", String(160), nullable=False, index=True),
        Column("state", String(60), nullable=False, index=True),
        Column("content_json", Text, nullable=False),
        Column("lead_digest", String(64), nullable=False, unique=True),
        Column("created_at", String(32), nullable=False),
    )
    return investigations, investigation_audit, acquisition_search_intents, evidence_leads
