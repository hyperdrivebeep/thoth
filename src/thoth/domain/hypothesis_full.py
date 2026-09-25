from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.research_projection import HypothesisGenerationDetails, PortfolioGenerationDetails
from thoth.domain.test_validity import Prespecification, ResearchMeasurementContract


class HypothesisRecord(DomainModel):
    hypothesis_revision_id: str
    hypothesis_id: str
    project_id: ProjectId
    object_id: str
    portfolio_id: str
    statement: str
    observed_problem: str
    primary_intent: str | None
    secondary_intents: tuple[str, ...] = ()
    intent_profile_refs: tuple[str, ...] = ()
    evidence_basis: str
    scope: dict[str, str]
    evidence_refs: tuple[str, ...]
    counterevidence_refs: tuple[str, ...] = ()
    counterevidence_queries: tuple[str, ...] = ()
    causal_profile: dict[str, object] = Field(default_factory=dict)
    development_stage: str = "DRAFT"
    empirical_appraisal: str = "UNASSESSED"
    freshness: str = "CURRENT"
    prespecification_state: str = "UNKNOWN"
    assumption_refs: tuple[str, ...] = ()
    prediction_refs: tuple[str, ...] = ()
    test_refs: tuple[str, ...] = ()
    quality_profile: dict[str, str] = Field(default_factory=dict)
    gateway_results: dict[str, str] = Field(default_factory=dict)
    invalidated_refs: tuple[str, ...] = ()
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    receipt_ref: str | None = None
    created_at: AwareDatetime
    generation_details: HypothesisGenerationDetails | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_stage_and_appraisal_separation(self) -> HypothesisRecord:
        if self.primary_intent is None and (
            self.development_stage != "DRAFT" or self.intent_profile_refs
        ):
            raise ValueError("an unclassified intent may remain only an incomplete draft")
        if self.empirical_appraisal == "ELIMINATED_WITHIN_SCOPE" and not self.test_refs:
            raise ValueError("scoped elimination requires a sealed test-validity chain")
        if self.development_stage == "PREDICTION_BOUND" and not self.prediction_refs:
            raise ValueError("prediction-bound stage requires an immutable prediction")
        return self


class HypothesisPortfolioRecord(DomainModel):
    portfolio_revision_id: str
    portfolio_id: str
    project_id: ProjectId
    object_id: str
    hypothesis_refs: tuple[str, ...]
    relation_refs: tuple[str, ...] = ()
    unknown_reserve: dict[str, object]
    coverage_state: str = "NOT_ASSESSED"
    diversity_state: str = "NOT_ASSESSED"
    discrimination_state: str = "NOT_ASSESSED"
    abstention_state: str = "AVAILABLE"
    quality_gaps: tuple[str, ...] = ()
    discrimination_matrix: tuple[dict[str, object], ...] = ()
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    receipt_ref: str | None = None
    created_at: AwareDatetime
    generation_details: PortfolioGenerationDetails | None = None
    schema_version: str = "1.0.0"


class HypothesisRelationRecord(DomainModel):
    relation_revision_id: str
    relation_id: str
    project_id: ProjectId
    portfolio_id: str
    source_hypothesis_id: str
    relation_type: str
    target_hypothesis_id: str
    evidence_refs: tuple[str, ...]
    semantic_role: str | None = None
    authority_state: str = "UNCLASSIFIED"
    active: bool = True
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class AuxiliaryAssumptionRecord(DomainModel):
    assumption_id: str
    project_id: ProjectId
    hypothesis_id: str
    statement: str
    role: str
    evidence_refs: tuple[str, ...]
    validation_route: str | None = None
    status: str = "UNVALIDATED"
    testability: str = "NOT_ASSESSED"
    assumption_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class PredictionRecord(DomainModel):
    prediction_id: str
    project_id: ProjectId
    hypothesis_id: str
    hypothesis_revision_digest: Sha256
    knowledge_cutoff: AwareDatetime
    prespecification_state: Prespecification
    conditions: dict[str, str]
    population_or_object: str
    time_window: str
    measurement_contract_ref: str
    assumption_refs: tuple[str, ...]
    expected_outcome: dict[str, object]
    discrimination_map: dict[str, object]
    observation_bound: bool = False
    prediction_fit: str = "NOT_OBSERVED"
    prediction_digest: Sha256
    created_at: AwareDatetime
    object_id: str | None = None
    measurement_contract: ResearchMeasurementContract | None = None
    measurement_contract_digest: Sha256 | None = None
    hypothesis_semantic_digest: Sha256 | None = None
    unbound_assumption_candidates: tuple[str, ...] = ()
    schema_version: str = "1.0.0"


class HypothesisTestBinding(DomainModel):
    test_binding_id: str
    project_id: ProjectId
    prediction_id: str
    execution_ref: str
    observation_refs: tuple[str, ...]
    test_validity_assessment_ref: str
    test_validity: str
    prediction_fit: str
    appraisal_mutation_allowed: bool = False
    binding_digest: Sha256
    created_at: AwareDatetime
    assessment_digest: Sha256 | None = None
    prediction_digest: Sha256 | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def invalid_test_cannot_mutate_appraisal(self) -> HypothesisTestBinding:
        if self.test_validity == "INVALID" and self.appraisal_mutation_allowed:
            raise ValueError("invalid test cannot mutate substantive appraisal")
        return self


class HypothesisAppraisalRecord(DomainModel):
    appraisal_id: str
    project_id: ProjectId
    hypothesis_id: str
    hypothesis_revision_digest: Sha256
    evidence_refs: tuple[str, ...]
    test_assessment_refs: tuple[str, ...]
    appraisal_scope: dict[str, str]
    appraisal: str
    rationale: str
    boundaries: tuple[str, ...]
    calibration: dict[str, object]
    substantive_update_applied: bool
    appraisal_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class HypothesisAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    hypothesis_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
