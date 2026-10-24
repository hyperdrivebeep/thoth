"""Prediction provenance and unassessed evidence relations, without review authority."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import EvidenceSpanId, Sha256, SourceVersionId
from thoth.domain.research_reference import RevisionRef
from thoth.domain.response_language import ResponseLanguage

DerivationRole = Literal[
    "OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE", "HYPOTHESIS_ONLY", "UNASSESSED"
]
GroundingStage = Literal["GENERATOR", "FILLER"]


class PredictionGroundingV1(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    derivation_role: DerivationRole
    rationale: str = Field(max_length=200)

    @model_validator(mode="after")
    def require_explanation_for_observation_or_inference(self) -> "PredictionGroundingV1":
        if (
            self.derivation_role in ("OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE")
            and not self.rationale.strip()
        ):
            raise ValueError("GROUNDING_RATIONALE_REQUIRED")
        return self


class RelationAssessmentV1(DomainModel):
    """Server-owned absence of assessment, never a semantic verdict or approval."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    assessment_status: Literal["UNASSESSED"] = "UNASSESSED"
    reason: Literal["SEMANTIC_ASSESSMENT_NOT_PERFORMED"] = "SEMANTIC_ASSESSMENT_NOT_PERFORMED"


class GroundingSourceBinding(DomainModel):
    span_id: EvidenceSpanId
    source_version_id: SourceVersionId
    text_sha256: Sha256


class GroundingStageInput(DomainModel):
    """One successful call's effective context and provider input have distinct digests."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    generating_request_ref: RevisionRef
    prompt_version: str = Field(min_length=1)
    output_schema_digest: Sha256
    effective_context_digest: Sha256
    provider_input_digest: Sha256
    response_language: ResponseLanguage | None
    language_policy_version: str | None
    cutoff_at: AwareDatetime
    model_policy_ref: str = Field(min_length=1)
    model_settings_digest: Sha256 | None
    behavior_basis: tuple[Sha256, ...] = ()
    max_output_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def language_origin(self) -> "GroundingStageInput":
        if (self.response_language is None) != (self.language_policy_version is None):
            raise ValueError("GROUNDING_LANGUAGE_POLICY_MISMATCH")
        if self.language_policy_version is not None and not self.language_policy_version.strip():
            raise ValueError("GROUNDING_LANGUAGE_POLICY_EMPTY")
        return self


class GroundingCallCapture(DomainModel):
    """Transient supplied evidence; persisted records retain bindings rather than this text."""

    metadata: GroundingStageInput
    evidence: tuple[EvidenceSpan, ...]


class GroundingRowDiagnostic(DomainModel):
    owner_hypothesis_id: str
    test_id: str
    hypothesis_id: str
    reason: str = Field(min_length=1)
    original_label_chars: int = Field(ge=0)
    original_basis_count: int = Field(ge=0)
