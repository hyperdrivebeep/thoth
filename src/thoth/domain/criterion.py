from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    AuthorityState,
    CriterionCompilationStatus,
    CriterionOrigin,
)
from thoth.domain.ids import CriterionId, EvidenceSpanId, ProjectId


class FormulaComputation(DomainModel):
    expression: str
    unit: str
    denominator: str | None = None
    aggregation: str | None = None


class ThresholdRule(DomainModel):
    operator: str
    target: Decimal | None = None
    tolerance: Decimal | None = None


class CriterionCandidate(DomainModel):
    criterion_id: CriterionId
    project_id: ProjectId
    name: str
    measured_construct: str
    computation: FormulaComputation | None = None
    context: dict[str, str]
    acceptance_rule: ThresholdRule | None = None
    evidence_refs: tuple[EvidenceSpanId, ...]
    authority_state: AuthorityState
    reference_candidate: bool = False
    evaluator_input_allowed: bool = False
    source_contract_revision: str | None = Field(default=None, min_length=64, max_length=64)
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def protect_reference_candidate(self) -> CriterionCandidate:
        if self.reference_candidate and self.evaluator_input_allowed:
            raise ValueError("reference candidate cannot be evaluator input")
        return self


class CriterionDraft(DomainModel):
    criterion_id: CriterionId
    project_id: ProjectId
    name: str
    measured_construct: str
    computation: FormulaComputation | None = None
    context: dict[str, str]
    acceptance_rule: ThresholdRule | None = None
    origin: CriterionOrigin
    field_evidence: dict[str, tuple[EvidenceSpanId, ...]] = Field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    uncertainty: str
    schema_version: str = "1.0.0"


class CriterionCompilationSignals(DomainModel):
    source_scope_confirmed: bool = False
    expert_semantics_confirmed: bool = False
    computation_verified: bool = False


class CriterionCompilationResult(DomainModel):
    status: CriterionCompilationStatus
    candidate: CriterionCandidate | None = None
    invalid_evidence_refs: tuple[EvidenceSpanId, ...] = ()
    missing_fields: tuple[str, ...] = ()
    expert_questions: tuple[str, ...] = ()
