"""Validated v5 prediction values; provider receive envelopes are a separate contract."""

from typing import Literal

from pydantic import Field, SerializeAsAny, model_validator

from thoth.domain.hypothesis_v4 import (
    DiscriminatingTestV4,
    ExpectedResultV4,
    HypothesisPortfolioV4,
    HypothesisV4,
    TableFillRecordV4,
)
from thoth.domain.hypothesis_v5_identity import prediction_identity_issues
from thoth.domain.prediction_grounding import (
    GroundingSourceBinding,
    GroundingStage,
    PredictionGroundingV1,
    RelationAssessmentV1,
)

CONTRACT_V5 = "hypothesis_portfolio.v5"
FILLER_V6 = "hypothesis_table_filler.v6"


class ExpectedResultV5(ExpectedResultV4):
    grounding: PredictionGroundingV1

    @model_validator(mode="after")
    def consistent_grounding(self) -> "ExpectedResultV5":
        role = self.grounding.derivation_role
        if len(set(self.basis)) != len(self.basis):
            raise ValueError("GROUNDING_DUPLICATE_SPAN")
        if self.knowledge_state == "UNKNOWN" and role != "UNASSESSED":
            raise ValueError("GROUNDING_UNKNOWN_ROLE_CONFLICT")
        needs_basis = role in ("OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE")
        if bool(self.basis) != needs_basis:
            raise ValueError("GROUNDING_ROLE_BASIS_CONFLICT")
        return self


class StoredExpectedResultV5(ExpectedResultV5):
    """Server bindings and assessment are deliberately absent from the model row."""

    source_stage: GroundingStage
    source_bindings: tuple[GroundingSourceBinding, ...] = Field(max_length=5)
    relation_assessment: RelationAssessmentV1

    @model_validator(mode="after")
    def exact_binding_order(self) -> "StoredExpectedResultV5":
        if self.basis != tuple(item.span_id for item in self.source_bindings):
            raise ValueError("GROUNDING_SOURCE_BINDING_MISMATCH")
        return self


class DiscriminatingTestV5(DiscriminatingTestV4):
    expected_by_hypothesis: tuple[ExpectedResultV5, ...] = Field(default=(), max_length=10)  # pyright: ignore[reportIncompatibleVariableOverride]

    @model_validator(mode="after")
    def unique_targets(self) -> "DiscriminatingTestV5":
        groups = [(self.test_id, tuple(row.hypothesis_id for row in self.expected_by_hypothesis))]
        if prediction_identity_issues(groups):
            raise ValueError("DUPLICATE_HYPOTHESIS")
        return self


class HypothesisV5(HypothesisV4):
    discriminating_tests: tuple[SerializeAsAny[DiscriminatingTestV5], ...]  # pyright: ignore[reportIncompatibleVariableOverride]

    @model_validator(mode="after")
    def unique_tests(self) -> "HypothesisV5":
        if prediction_identity_issues([(test.test_id, ()) for test in self.discriminating_tests]):
            raise ValueError("DUPLICATE_TEST_ID")
        return self


class HypothesisPortfolioV5(HypothesisPortfolioV4):
    hypotheses: tuple[SerializeAsAny[HypothesisV5], ...]  # pyright: ignore[reportIncompatibleVariableOverride]


class TableFillRecordV5(TableFillRecordV4):
    contract_version: Literal["hypothesis_table_filler.v6"] = FILLER_V6  # pyright: ignore[reportIncompatibleVariableOverride]
