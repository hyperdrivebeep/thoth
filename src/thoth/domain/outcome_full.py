from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class OutcomeProfileRecord(DomainModel):
    profile_ref: str
    version: int = Field(ge=1)
    name: str
    action_purposes: tuple[str, ...]
    required_evidence: tuple[str, ...]
    comparator_contract: str
    window_contract: str
    evaluator_contract: str
    effect_dimensions: tuple[str, ...]
    attribution_standard: str
    closure_criteria: dict[str, str]
    enabled: bool = True
    authority_owner: str
    profile_digest: Sha256
    schema_version: str = "1.0.0"


class OutcomeSeriesRecord(DomainModel):
    series_revision_id: str
    outcome_series_id: str
    project_id: ProjectId
    object_id: str
    action_plan_revision_digest: Sha256
    planned_execution_ref: str | None = None
    profile_ref: str
    comparison_baseline_set_digest: Sha256
    assessment_windows: tuple[dict[str, object], ...]
    phase_states: dict[str, str]
    observation_refs_by_phase: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    observation_completeness_by_phase: dict[str, str] = Field(default_factory=dict)
    assessment_refs: tuple[str, ...] = ()
    final_within_scope_state: str = "NOT_ASSESSED"
    revision: int = 0
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class OutcomeAssessmentRecord(DomainModel):
    assessment_revision_id: str
    outcome_assessment_id: str
    project_id: ProjectId
    outcome_series_id: str
    object_id: str
    assessment_phase: str
    plan_revision_digest: Sha256
    execution_ref: str | None = None
    baseline_set_digest: Sha256
    profile_ref: str
    profile_version: int
    expected_observation_refs: tuple[str, ...] = ()
    actual_observation_refs: tuple[str, ...]
    comparator_refs: tuple[str, ...]
    assumptions: tuple[str, ...]
    criterion_refs: tuple[str, ...] = ()
    hypothesis_refs: tuple[str, ...] = ()
    residual_effect_refs: tuple[str, ...] = ()
    lifecycle: str = "ASSESSED"
    validity: str = "NOT_ASSESSED"
    objective_attainment: str = "NOT_ASSESSED"
    attribution_state: str = "NOT_ASSESSED"
    follow_up_state: str = "MORE_EVIDENCE_REQUIRED"
    limitations: tuple[str, ...] = ()
    changed_dimensions: tuple[str, ...] = ()
    attribution_ref: str | None = None
    change_set_refs: tuple[str, ...] = ()
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def invalid_outcome_cannot_claim_attainment(self) -> OutcomeAssessmentRecord:
        if self.validity == "INVALID" and self.objective_attainment not in {
            "NOT_ASSESSED",
            "NOT_APPLICABLE",
        }:
            raise ValueError("invalid Outcome cannot claim objective attainment")
        return self


class AttributionAssessmentRecord(DomainModel):
    attribution_assessment_id: str
    project_id: ProjectId
    outcome_assessment_id: str
    method: str
    contextual_factor_refs: tuple[str, ...]
    counterfactual_evidence_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    supported_level: str
    limitations: tuple[str, ...]
    attribution_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class OutcomeChangeSetRecord(DomainModel):
    outcome_change_set_id: str
    project_id: ProjectId
    outcome_assessment_id: str
    proposed_entity_changes: dict[str, object]
    impact_policy_ref: str
    expected_project_head_set: Sha256
    candidate_revisions: tuple[dict[str, object], ...]
    impact_propagation_plan: dict[str, object]
    validity_restrictions: tuple[str, ...]
    status: str = "PROPOSED_NOT_APPLIED"
    change_set_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class OutcomeImpactRecord(DomainModel):
    impact_assessment_id: str
    project_id: ProjectId
    outcome_series_id: str
    broader_window: str
    impact_profile_ref: str
    evidence_refs: tuple[str, ...]
    attribution_design_ref: str
    status: str
    contextual_factors: tuple[str, ...]
    limitations: tuple[str, ...]
    impact_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class OutcomeAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    subject_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
