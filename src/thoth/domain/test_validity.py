"""Research measurement contracts and immutable, scope-bound test assessments."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, cast

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import Sha256

Prespecification = Literal["A_PRIORI", "BLINDED_AMENDMENT", "POST_HOC", "RETRODICTIVE", "UNKNOWN"]
TestValidity = Literal["VALID", "LIMITED", "INVALID", "NOT_ASSESSABLE"]
PredictionFit = Literal["MATCH", "PARTIAL_MATCH", "MISMATCH", "NOT_OBSERVED", "INCONCLUSIVE"]


class ExpectedRange(DomainModel):
    type: Literal["RANGE"]
    measure: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1, max_length=80)
    lower: Decimal = Field(allow_inf_nan=False)
    upper: Decimal = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_bounds(self) -> ExpectedRange:
        if self.lower > self.upper:
            raise ValueError("PREDICTION_RANGE_REVERSED")
        return self


class PredictionProposal(DomainModel):
    measurement_contract_ref: str = Field(min_length=1, max_length=260)
    prespecification_state: Prespecification
    conditions: dict[str, str]
    expected_outcome: ExpectedRange


class ResearchMeasurementContract(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    record_type: Literal["RESEARCH_MEASUREMENT_CONTRACT"]
    contract_id: str = Field(min_length=1, max_length=160)
    method: str = Field(min_length=1, max_length=80)
    procedure_version: str = Field(min_length=1, max_length=160)
    measure: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1, max_length=80)
    conditions: dict[str, str]
    minimum_samples: int = Field(ge=1, le=10_000)
    maximum_samples: int = Field(ge=1, le=10_000)
    image_digest: str = Field(min_length=1, max_length=500)
    runtime_version: str = Field(min_length=1, max_length=160)
    official_evaluator_input_allowed: Literal[False] = False

    @model_validator(mode="after")
    def bounded_samples(self) -> ResearchMeasurementContract:
        if self.minimum_samples > self.maximum_samples:
            raise ValueError("MEASUREMENT_SAMPLE_BOUNDS_REVERSED")
        if not self.conditions:
            raise ValueError("MEASUREMENT_CONDITIONS_REQUIRED")
        return self


class ResearchMeasurementObservation(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    record_type: Literal["RESEARCH_MEASUREMENT_OBSERVATION"]
    contract_digest: Sha256
    procedure_version: str = Field(min_length=1, max_length=160)
    measure: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1, max_length=80)
    conditions: dict[str, str]
    samples: tuple[Decimal, ...] = Field(min_length=1, max_length=10_000)
    input_digests: tuple[Sha256, ...]
    observed_at: AwareDatetime
    assumption_results: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def finite_samples(self) -> ResearchMeasurementObservation:
        if any(not value.is_finite() for value in self.samples):
            raise ValueError("MEASUREMENT_SAMPLE_NOT_FINITE")
        return self


class TestValidityAssessment(DomainModel):
    assessment_id: str
    project_id: str
    object_id: str
    hypothesis_id: str
    hypothesis_revision_digest: Sha256
    hypothesis_semantic_digest: Sha256
    prediction_id: str
    prediction_digest: Sha256
    execution_ref: str
    attempt_ref: str
    plan_id: str
    plan_revision_digest: Sha256
    observation_refs: tuple[str, ...]
    measurement_contract_ref: str
    measurement_contract_digest: Sha256
    sandbox_receipt_digest: Sha256
    input_context_digest: Sha256
    policy_digest: Sha256
    knowledge_cutoff: AwareDatetime
    scope: dict[str, str]
    test_validity: TestValidity
    prediction_fit: PredictionFit
    measured_value: Decimal | None = None
    reasons: tuple[str, ...]
    producer_ref: str
    runtime_profile: str
    execution_security_tier: str
    raw_observation_digest: Sha256
    measurement_output_digest: Sha256
    substantive_update_allowed: bool
    official_criterion_disposition: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"
    semantic_truth_certified: Literal[False] = False
    created_at: AwareDatetime
    assessment_digest: Sha256
    schema_version: Literal["1.0.0"] = "1.0.0"

    @model_validator(mode="after")
    def preserve_invalid_and_nonconfirmatory_boundary(self) -> TestValidityAssessment:
        if self.test_validity not in {"VALID", "LIMITED"} and self.substantive_update_allowed:
            raise ValueError("INVALID_TEST_CANNOT_UPDATE_APPRAISAL")
        if self.measured_value is not None and not self.measured_value.is_finite():
            raise ValueError("MEASUREMENT_VALUE_NOT_FINITE")
        return self


def hypothesis_semantic_digest(value: Mapping[str, object]) -> Sha256:
    """Binding/appraisal metadata does not redefine the sealed hypothesis basis."""
    fields = (
        "project_id",
        "object_id",
        "hypothesis_id",
        "portfolio_id",
        "statement",
        "observed_problem",
        "primary_intent",
        "secondary_intents",
        "scope",
        "evidence_refs",
        "counterevidence_refs",
        "assumption_refs",
        "causal_profile",
    )
    payload = {key: value.get(key) for key in fields}
    details = value.get("generation_details")
    if isinstance(details, dict):
        generation = cast(dict[str, object], details)
        payload["generation_basis"] = {
            key: generation.get(key)
            for key in (
                "primary_locus",
                "contributing_loci",
                "causal_depth",
                "assumptions",
                "uncertainty",
                "predicted_observation_candidates",
                "discriminating_test_candidates",
            )
        }
    return domain_digest("HYPOTHESIS_SEMANTIC_BASIS", "1.0.0", canonical_payload(payload))
