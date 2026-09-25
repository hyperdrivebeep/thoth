from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.reference import ReferenceInquiry


class CriterionProfileRecord(DomainModel):
    profile_ref: str
    version: int = Field(ge=1)
    name: str
    domain_hint: str
    required_fields: tuple[str, ...]
    conditional_fields: tuple[str, ...] = ()
    allowed_computation_types: tuple[str, ...]
    authority_policy: dict[str, str]
    applicability_terms: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    measurement_validity: dict[str, str] = Field(default_factory=dict)
    uncertainty_policy: dict[str, str] = Field(default_factory=dict)
    decision_rules: tuple[str, ...] = ()
    change_authority: dict[str, str] = Field(default_factory=dict)
    entry_criteria: tuple[str, ...] = ()
    exit_criteria: tuple[str, ...] = ()
    optional_model_mapping_allowed: bool = True
    enabled: bool = True
    profile_digest: Sha256
    schema_version: str = "1.0.0"


class CriterionContractRecord(DomainModel):
    criterion_revision_id: str
    criterion_id: str
    project_id: ProjectId
    thread_id: str | None = None
    identity: dict[str, str]
    profile_refs: tuple[str, ...]
    goal_requirement_refs: tuple[str, ...] = ()
    construct_outcome_definition: str
    verification_spec: dict[str, object]
    computation_spec: dict[str, object] | None = None
    context_spec: dict[str, str] = Field(default_factory=dict)
    acceptance_rule: dict[str, object] | None = None
    required_evidence: tuple[str, ...]
    field_evidence_map: dict[str, tuple[str, ...]]
    field_authority_and_version: dict[str, dict[str, str]]
    governance: dict[str, object]
    evaluator_or_procedure_binding: dict[str, object] | None = None
    result_and_uncertainty: dict[str, object] | None = None
    reference_inquiry: ReferenceInquiry | None = None
    lifecycle: str = "DRAFT"
    completeness: str = "INCOMPLETE"
    consistency: str = "CONSISTENT"
    comparability: str = "NOT_ASSESSED"
    technical_executability: str = "NOT_EXECUTABLE"
    measurement_validity: str = "NOT_ASSESSED"
    usage_authorization: str = "NOT_AUTHORIZED"
    prespecification: str = "UNKNOWN"
    missing_fields: tuple[str, ...] = ()
    invalidation_state: str = "CURRENT"
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    receipt_ref: str | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def keep_axes_separate(self) -> CriterionContractRecord:
        computation_type = (
            None if self.computation_spec is None else self.computation_spec.get("type")
        )
        if (
            self.technical_executability == "DETERMINISTICALLY_EXECUTABLE"
            and computation_type != "FORMULA"
        ):
            raise ValueError("only FORMULA can be deterministically executable in Criteria")
        if self.usage_authorization == "AUTHORIZED_EVALUATOR_INPUT" and (
            self.completeness != "COMPLETE"
            or self.consistency != "CONSISTENT"
            or self.invalidation_state != "CURRENT"
        ):
            raise ValueError("authorized evaluator input requires complete current contract")
        return self


class CriterionReferenceCandidate(DomainModel):
    reference_candidate_id: str
    project_id: ProjectId
    criterion_id: str
    source_lineage: tuple[str, ...]
    mapped_value: Decimal | None = None
    unit: str | None = None
    normalization: str
    uncertainty: str
    scenarios: tuple[str, ...]
    applicability_gaps: tuple[str, ...]
    assumptions: tuple[str, ...]
    authorization_state: str = "NOT_AUTHORIZED"
    evaluator_input_allowed: bool = False
    reference_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def reference_never_authorizes(self) -> CriterionReferenceCandidate:
        if self.authorization_state != "NOT_AUTHORIZED" or self.evaluator_input_allowed:
            raise ValueError("reference candidate cannot be official evaluator input")
        return self


class CriterionConflictRecord(DomainModel):
    conflict_id: str
    project_id: ProjectId
    criterion_id: str
    field_path: str
    candidate_values: tuple[object, ...]
    evidence_refs: tuple[str, ...]
    status: str = "OPEN"
    impact: dict[str, object]
    conflict_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class CriterionAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    criterion_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class CriterionProfileDecisionState(StrEnum):
    SELECTED = "SELECTED"
    PROFILE_DECISION_REQUIRED = "PROFILE_DECISION_REQUIRED"


class CriterionProfileDecision(DomainModel):
    decision_id: str
    project_id: ProjectId
    state: CriterionProfileDecisionState
    selected_profile_refs: tuple[str, ...]
    candidate_profile_refs: tuple[str, ...]
    source_span_refs: tuple[str, ...]
    source_scores: dict[str, int]
    decision_dimension_changes: dict[str, tuple[str, ...]]
    applicability_evidence: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    profile_decision_required: bool
    hold_reason: str | None = None
    decision_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
