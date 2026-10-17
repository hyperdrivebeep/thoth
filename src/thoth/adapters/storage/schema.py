from __future__ import annotations

from sqlalchemy import Column, ForeignKey, MetaData, Table, UniqueConstraint
from sqlalchemy.types import Integer, String, Text

from thoth.adapters.storage.action_schema import define_action_tables
from thoth.adapters.storage.artifact_schema import define_artifact_tables
from thoth.adapters.storage.baseline_schema import define_baseline_tables
from thoth.adapters.storage.behavior_catalog_schema import define_behavior_catalog_tables
from thoth.adapters.storage.criterion_schema import define_criterion_tables
from thoth.adapters.storage.decision_object_schema import define_decision_object_tables
from thoth.adapters.storage.evidence_schema import define_evidence_tables
from thoth.adapters.storage.execution_schema import define_execution_tables
from thoth.adapters.storage.hypothesis_schema import define_hypothesis_tables
from thoth.adapters.storage.investigation_schema import define_investigation_tables
from thoth.adapters.storage.outcome_schema import define_outcome_tables

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("cutoff_at", String(32), nullable=False),
    Column("lifecycle", String(40), nullable=False),
    Column("overlay", String(160), nullable=False, server_default="default"),
    Column("policy_ref", String(160), nullable=False),
    Column("created_at", String(32), nullable=False),
    Column("revision", Integer, nullable=False, server_default="0"),
)

organizations = Table(
    "organizations",
    metadata,
    Column("organization_id", String(160), primary_key=True),
    Column("name", Text, nullable=False),
    Column("kind", String(80), nullable=False),
    Column("created_at", String(32), nullable=False),
)

project_roles = Table(
    "project_roles",
    metadata,
    Column("role_assignment_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("actor_id", String(160), nullable=False, index=True),
    Column("organization_id", String(160), nullable=True),
    Column("role", String(160), nullable=False),
    Column("scope", String(260), nullable=False),
    Column("authority_tags_json", Text, nullable=False),
    Column("state", String(40), nullable=False),
    Column("created_at", String(32), nullable=False),
    Column("revoked_at", String(32), nullable=True),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("session_id", String(200), primary_key=True),
    Column("actor_id", String(160), nullable=False, index=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("role_assignment_id", String(160), nullable=False),
    Column("token_digest", String(64), nullable=False, unique=True),
    Column("state", String(40), nullable=False),
    Column("content_json", Text, nullable=False),
    Column("expires_at", String(40), nullable=False),
)

field_protocol_seals = Table(
    "field_protocol_seals",
    metadata,
    Column("protocol_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("protocol_digest", String(64), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
)

field_sessions = Table(
    "field_sessions",
    metadata,
    Column("session_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("protocol_digest", String(64), nullable=False),
    Column("state", String(40), nullable=False),
    Column("content_json", Text, nullable=False),
)

field_events = Table(
    "field_events",
    metadata,
    Column("event_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("session_id", String(200), nullable=False, index=True),
    Column("event_type", String(80), nullable=False),
    Column("content_json", Text, nullable=False),
)

field_session_metrics = Table(
    "field_session_metrics",
    metadata,
    Column("session_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("content_json", Text, nullable=False),
)

field_scores = Table(
    "field_scores",
    metadata,
    Column("score_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("session_id", String(200), nullable=False, index=True),
    Column("content_json", Text, nullable=False),
)

field_exports = Table(
    "field_exports",
    metadata,
    Column("export_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("protocol_digest", String(64), nullable=False),
    Column("bundle_digest", String(64), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
)

project_head_sets, baseline_candidates, baseline_sets, baseline_decisions = define_baseline_tables(
    metadata
)

behavior_artifacts, behavior_registry = define_behavior_catalog_tables(metadata)

tui_sessions = Table(
    "tui_sessions",
    metadata,
    Column("session_id", String(200), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("content_json", Text, nullable=False),
)

workstreams = Table(
    "workstreams",
    metadata,
    Column("workstream_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("name", Text, nullable=False),
    Column("parent_workstream_id", String(160), nullable=True),
    Column("owner_role_assignment_id", String(160), nullable=True),
    Column("state", String(40), nullable=False),
    Column("revision", Integer, nullable=False, server_default="0"),
)

project_policies = Table(
    "project_policies",
    metadata,
    Column("policy_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("created_at", String(32), nullable=False),
    UniqueConstraint("project_id", "version", name="uq_project_policy_version"),
)

project_references = Table(
    "project_references",
    metadata,
    Column("reference_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("origin_project_id", String(160), nullable=False),
    Column("origin_revision", String(160), nullable=False),
    Column("rights_status", String(80), nullable=False),
    Column("scope", String(260), nullable=False),
    Column("authority_status", String(80), nullable=False),
    Column("created_at", String(32), nullable=False),
)

source_bindings = Table(
    "source_bindings",
    metadata,
    Column("binding_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("artifact_id", String(160), nullable=False, index=True),
    Column("capability", String(40), nullable=False),
    Column("state", String(40), nullable=False),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
    UniqueConstraint("project_id", "artifact_id", name="uq_source_binding_artifact"),
)

threads = Table(
    "threads",
    metadata,
    Column("thread_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("cycle_id", String(160), nullable=False),
    Column("problem", Text, nullable=False),
    Column("display_name", Text, nullable=False, server_default=""),
    Column("scope_json", Text, nullable=False, server_default="{}"),
    Column("parent_thread_id", String(160), nullable=True),
    Column("fork_origin", String(160), nullable=True),
    Column("lifecycle", String(60), nullable=False),
    Column("execution_state", String(60), nullable=False),
    Column("current_object_ids_json", Text, nullable=False),
    Column("working_head_digest", String(64), nullable=False),
    Column("revision", Integer, nullable=False, server_default="0"),
    Column("created_at", String(32), nullable=False, server_default="1970-01-01T00:00:00Z"),
    Column("updated_at", String(32), nullable=False, server_default="1970-01-01T00:00:00Z"),
)

thread_activities = Table(
    "thread_activities",
    metadata,
    Column("activity_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("thread_id", String(160), nullable=False, index=True),
    Column("cycle_id", String(160), nullable=False),
    Column("event_type", String(120), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("actor_id", String(160), nullable=False),
    Column("activity_digest", String(64), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
)

thread_checkpoints = Table(
    "thread_checkpoints",
    metadata,
    Column("checkpoint_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("thread_id", String(160), nullable=False, index=True),
    Column("cycle_id", String(160), nullable=False),
    Column("head_set_digest", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("checkpoint_digest", String(64), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
)

thread_inputs = Table(
    "thread_inputs",
    metadata,
    Column("input_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("thread_id", String(160), nullable=False, index=True),
    Column("kind", String(40), nullable=False),
    Column("text", Text, nullable=False),
    Column("state", String(40), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("created_at", String(32), nullable=False),
    UniqueConstraint("thread_id", "ordinal", name="uq_thread_input_ordinal"),
)

investigations, investigation_audit, acquisition_search_intents, evidence_leads = (
    define_investigation_tables(metadata)
)

(
    evidence_sources,
    evidence_observations,
    evidence_links,
    evidence_conflicts,
    evidence_audit,
    span_corrections,
) = define_evidence_tables(metadata)

(
    criterion_profiles,
    criterion_contracts,
    criterion_references,
    criterion_conflicts,
    criterion_audit,
) = define_criterion_tables(metadata)

(
    object_profiles,
    object_candidates,
    decision_objects,
    object_relations,
    object_attention,
    object_audit,
) = define_decision_object_tables(metadata)

(
    hypothesis_records,
    hypothesis_portfolios,
    hypothesis_relations,
    hypothesis_assumptions,
    hypothesis_predictions,
    hypothesis_test_bindings,
    hypothesis_appraisals,
    hypothesis_audit,
) = define_hypothesis_tables(metadata)

action_records, action_portfolios, action_plans, action_authorizations, action_audit = (
    define_action_tables(metadata)
)

(
    plan_executions,
    step_execution_attempts,
    execution_effects,
    execution_reconciliations,
    execution_audit,
) = define_execution_tables(metadata)

(
    outcome_profiles,
    outcome_series,
    outcome_assessments,
    outcome_attributions,
    outcome_change_sets,
    outcome_impacts,
    outcome_audit,
) = define_outcome_tables(metadata)

control_records = Table(
    "control_records",
    metadata,
    Column("control_revision_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("namespace", String(80), nullable=False, index=True),
    Column("record_type", String(120), nullable=False, index=True),
    Column("record_id", String(160), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("state", String(80), nullable=False, index=True),
    Column("content_json", Text, nullable=False),
    Column("record_digest", String(64), nullable=False, unique=True),
    Column("supersedes_digest", String(64), nullable=True),
    Column("created_at", String(32), nullable=False),
)

entity_snapshots = Table(
    "entity_snapshots",
    metadata,
    Column("snapshot_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("entity_type", String(60), nullable=False, index=True),
    Column("entity_id", String(160), nullable=False, index=True),
    Column("schema_version", String(40), nullable=False),
    Column("content_json", Text, nullable=False),
    Column("content_digest", String(64), nullable=False),
)

semantic_revisions = Table(
    "semantic_revisions",
    metadata,
    Column("revision_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("entity_type", String(60), nullable=False, index=True),
    Column("entity_id", String(160), nullable=False, index=True),
    Column("snapshot_id", String(160), ForeignKey("entity_snapshots.snapshot_id"), nullable=False),
    Column("actor_json", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("evidence_refs_json", Text, nullable=False),
    Column("affected_refs_json", Text, nullable=False),
    Column("revision_digest", String(64), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
    Column("schema_version", String(40), nullable=False),
)

revision_parents = Table(
    "revision_parents",
    metadata,
    Column(
        "revision_id",
        String(160),
        ForeignKey("semantic_revisions.revision_id"),
        primary_key=True,
    ),
    Column("ordinal", Integer, primary_key=True),
    Column("parent_digest", String(64), nullable=False),
)

working_heads = Table(
    "working_heads",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("aggregate_key", String(260), primary_key=True),
    Column("revision_digest", String(64), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

receipts = Table(
    "receipts",
    metadata,
    Column("receipt_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("receipt_type", String(40), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("receipt_digest", String(64), nullable=False, unique=True),
    Column("previous_transition_digest", String(64), nullable=True),
    Column("created_at", String(32), nullable=False),
)
receipt_dag_nodes = Table(
    "receipt_dag_nodes",
    metadata,
    Column("node_id", String(200), primary_key=True),
    Column("project_id", String(160), primary_key=True, index=True),
    Column("kind", String(40), nullable=False, index=True),
    Column("content_json", Text, nullable=False),
    Column("node_digest", String(64), nullable=False, unique=True),
)
receipt_dag_edges = Table(
    "receipt_dag_edges",
    metadata,
    Column("edge_id", String(200), primary_key=True),
    Column("project_id", String(160), primary_key=True, index=True),
    Column("parent_node_id", String(200), nullable=False),
    Column("child_node_id", String(200), nullable=False),
    Column("content_json", Text, nullable=False),
    Column("edge_digest", String(64), nullable=False, unique=True),
)
receipt_dag_manifests = Table(
    "receipt_dag_manifests",
    metadata,
    Column("manifest_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
    Column("manifest_digest", String(64), nullable=False),
)
receipt_dag_bundles = Table(
    "receipt_dag_bundles",
    metadata,
    Column("bundle_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("content_json", Text, nullable=False),
    Column("bundle_digest", String(64), nullable=False, unique=True),
)
receipt_dag_verifications = Table(
    "receipt_dag_verifications",
    metadata,
    Column("verification_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("subject_id", String(200), nullable=False, index=True),
    Column("content_json", Text, nullable=False),
    Column("verification_digest", String(64), nullable=False, unique=True),
)

structural_nodes, artifacts, artifact_versions, evidence_spans = define_artifact_tables(metadata)

operations = Table(
    "operations",
    metadata,
    Column("operation_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("method", String(160), nullable=False),
    Column("idempotency_key", String(260), nullable=False),
    Column("scope_digest", String(64), nullable=False),
    Column("owner_actor_id", String(160), nullable=True),
    Column("owner_session_id", String(160), nullable=True),
    Column("owner_role_assignment_id", String(160), nullable=True),
    Column("owner_data_scopes_json", Text, nullable=False, server_default="[]"),
    Column("state", String(40), nullable=False),
    Column("result_json", Text, nullable=True),
    Column("error_json", Text, nullable=True),
    Column("created_at", String(32), nullable=False),
    Column("completed_at", String(32), nullable=True),
    Column("epoch", Integer, nullable=False, server_default="0"),
    UniqueConstraint(
        "project_id", "method", "idempotency_key", name="uq_operation_idempotency_scope"
    ),
)

events = Table(
    "events",
    metadata,
    Column("event_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("operation_id", String(160), ForeignKey("operations.operation_id"), nullable=False),
    Column("event_type", String(160), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("previous_event_digest", String(64), nullable=True),
    Column("event_digest", String(64), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column("checkpoint_id", String(160), primary_key=True),
    Column("operation_id", String(160), ForeignKey("operations.operation_id"), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("checkpoint_digest", String(64), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("method", String(160), primary_key=True),
    Column("idempotency_key", String(260), primary_key=True),
    Column("scope_digest", String(64), nullable=False),
    Column("operation_id", String(160), ForeignKey("operations.operation_id"), nullable=False),
)

relations = Table(
    "relations",
    metadata,
    Column("relation_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("source_ref", String(260), nullable=False, index=True),
    Column("relation_type", String(80), nullable=False),
    Column("target_ref", String(260), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("revision_digest", String(64), nullable=False),
)

dependency_states = Table(
    "dependency_states",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("entity_ref", String(260), primary_key=True),
    Column("status", String(60), nullable=False),
    Column("caused_by_revision", String(64), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

memory_records = Table(
    "memory_records",
    metadata,
    Column("memory_id", String(160), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("payload_mode", String(60), nullable=False),
    Column("kind", String(60), nullable=False),
    Column("owner_revision_ref", String(160), nullable=False),
    Column("source_ref", String(260), nullable=True),
    Column("assertion", Text, nullable=True),
    Column("recall_eligibility", String(60), nullable=False),
    Column("lifecycle", String(60), nullable=False),
    Column("revision_digest", String(64), nullable=False),
)

memory_revision_ledger = Table(
    "memory_revision_ledger",
    metadata,
    Column("memory_revision_id", String(200), primary_key=True),
    Column("memory_id", String(160), nullable=False),
    Column("project_id", String(160), nullable=False, index=True),
    Column("transition", String(40), nullable=False),
    Column("owner_revision_ref", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
    Column("revision_digest", String(64), nullable=False, unique=True),
    UniqueConstraint("project_id", "memory_id", name="uq_memory_revision_source_memory"),
)

memory_transition_receipts = Table(
    "memory_transition_receipts",
    metadata,
    Column("receipt_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("memory_revision_id", String(200), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
    Column("receipt_digest", String(64), nullable=False, unique=True),
)

memory_context_packs = Table(
    "memory_context_packs",
    metadata,
    Column("context_pack_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("thread_id", String(160), nullable=False),
    Column("content_json", Text, nullable=False),
    Column("query_digest", String(64), nullable=False),
)

memory_projections = Table(
    "memory_projections",
    metadata,
    Column("projection_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("projection_type", String(40), nullable=False),
    Column("content_json", Text, nullable=False),
    Column("rebuild_checkpoint", String(64), nullable=False),
    UniqueConstraint("project_id", "projection_type", name="uq_memory_projection_type"),
)
