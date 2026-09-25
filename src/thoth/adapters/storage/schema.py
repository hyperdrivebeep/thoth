from __future__ import annotations

from sqlalchemy import Column, ForeignKey, MetaData, Table, UniqueConstraint
from sqlalchemy.types import Integer, String, Text

from thoth.adapters.storage.artifact_schema import define_artifact_tables

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

project_head_sets = Table(
    "project_head_sets",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("head_set_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)

baseline_candidates = Table(
    "baseline_candidates",
    metadata,
    Column("candidate_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("scope", String(80), nullable=False),
    Column("state", String(60), nullable=False),
    Column("candidate_digest", String(64), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
)

baseline_sets = Table(
    "baseline_sets",
    metadata,
    Column("baseline_set_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("scope", String(80), nullable=False),
    Column("lifecycle", String(40), nullable=False),
    Column("baseline_set_digest", String(64), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
)

baseline_decisions = Table(
    "baseline_decisions",
    metadata,
    Column("decision_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("candidate_id", String(200), nullable=False),
    Column("decision", String(20), nullable=False),
    Column("decision_digest", String(64), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
)

behavior_artifacts = Table(
    "behavior_artifacts",
    metadata,
    Column("artifact_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("kind", String(80), nullable=False),
    Column("state", String(40), nullable=False),
    Column("content_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
    UniqueConstraint(
        "project_id",
        "kind",
        "content_digest",
        name="uq_behavior_artifact_content",
    ),
)

behavior_registry = Table(
    "behavior_registry",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("kind", String(80), primary_key=True),
    Column("active_digest", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("content_json", Text, nullable=False),
)

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
