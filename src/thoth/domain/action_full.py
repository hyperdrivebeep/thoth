from __future__ import annotations

from pydantic import AwareDatetime, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.research_projection import ActionGenerationDetails, ActionPlanGenerationDetails


class ActionRecord(DomainModel):
    action_revision_id: str
    action_id: str
    project_id: ProjectId
    object_id: str
    portfolio_id: str
    hypothesis_refs: tuple[str, ...] = ()
    primary_purpose: str | None
    secondary_purposes: tuple[str, ...] = ()
    specification: dict[str, object]
    evidence_refs: tuple[str, ...]
    expected_observation_or_change: dict[str, object]
    effect_vector: dict[str, object]
    impact_set: dict[str, object]
    risk_tier: str
    required_processes: tuple[str, ...]
    required_roles: tuple[str, ...]
    proposal_state: str = "CANDIDATE"
    decision_state: str = "NOT_EVALUATED"
    policy_state: str = "NOT_CLASSIFIED"
    authorization_state: str = "NOT_REQUIRED"
    freshness: str = "CURRENT"
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    receipt_ref: str | None = None
    created_at: AwareDatetime
    generation_details: ActionGenerationDetails | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_policy_boundary(self) -> ActionRecord:
        if self.primary_purpose is None and self.proposal_state != "DRAFT":
            raise ValueError("an unclassified action purpose may remain only an incomplete draft")
        if self.risk_tier == "R4" and self.policy_state != "PROHIBITED":
            raise ValueError("R4 Action must remain prohibited")
        if self.risk_tier == "R3" and not self.required_roles:
            raise ValueError("R3 Action requires non-fungible authority roles")
        if self.risk_tier in {"R0", "R1", "R2"} and self.effect_vector.get("external_write"):
            raise ValueError("R0-R2 cannot perform external writes")
        return self


class ActionPortfolioRecord(DomainModel):
    portfolio_revision_id: str
    portfolio_id: str
    project_id: ProjectId
    object_id: str
    action_refs: tuple[str, ...]
    decision_need: str
    mandatory_criteria: tuple[dict[str, object], ...]
    enhancing_criteria: tuple[dict[str, object], ...]
    evaluation_method: str = "NOT_EVALUATED"
    scenario_results: tuple[dict[str, object], ...] = ()
    uncertainty: str = "UNASSESSED"
    sensitivity: str = "UNASSESSED"
    value_of_information: str = "UNASSESSED"
    decision_state: str = "NOT_EVALUATED"
    recommendation_ref: str | None = None
    selected_action_ref: str | None = None
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ActionPlanRecord(DomainModel):
    plan_revision_id: str
    plan_id: str
    project_id: ProjectId
    object_id: str
    selected_action_refs: tuple[str, ...]
    steps: tuple[dict[str, object], ...]
    dependency_edges: tuple[dict[str, str], ...]
    cumulative_impact: dict[str, object]
    required_process_union: tuple[str, ...]
    required_role_union: tuple[str, ...]
    auto_executable_frontier: tuple[str, ...]
    point_of_no_return_steps: tuple[str, ...]
    authorization_refs: tuple[str, ...] = ()
    validation_state: str = "CANDIDATE"
    freshness: str = "CURRENT"
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    generation_details: ActionPlanGenerationDetails | None = None
    schema_version: str = "1.0.0"


class AuthorizationEnvelopeRecord(DomainModel):
    authorization_revision_id: str
    authorization_id: str
    project_id: ProjectId
    plan_id: str
    step_id: str
    plan_revision_digest: Sha256
    predecessor_output_digests: tuple[Sha256, ...]
    target_baseline_digests: tuple[Sha256, ...]
    policy_version: str
    exact_scope_digest: Sha256
    required_roles: tuple[str, ...]
    state: str = "PENDING"
    decision_history: tuple[dict[str, object], ...] = ()
    expires_at: AwareDatetime
    single_use: bool = True
    consumed_at: AwareDatetime | None = None
    stale_reason: str | None = None
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def content_bound_and_single_use(self) -> AuthorizationEnvelopeRecord:
        if self.state == "CONSUMED" and self.consumed_at is None:
            raise ValueError("consumed authorization requires consumed_at")
        return self


class ActionAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    subject_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
