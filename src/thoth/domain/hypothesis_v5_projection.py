"""Canonical v5 details with immutable per-stage provenance, not a second evidence owner."""

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.hypothesis_v5 import (
    CONTRACT_V5,
    FILLER_V6,
    StoredExpectedResultV5,
    TableFillRecordV5,
)
from thoth.domain.hypothesis_v5_identity import prediction_identity_issues
from thoth.domain.prediction_grounding import (
    GroundingRowDiagnostic,
    GroundingStage,
    GroundingStageInput,
)
from thoth.domain.research_projection import HypothesisGenerationDetails, PortfolioGenerationDetails
from thoth.domain.research_reference import RevisionRef
from thoth.domain.response_language import ResponseLanguage


def _validate_stages(
    stages: Mapping[GroundingStage, GroundingStageInput],
    request_ref: RevisionRef,
    language: ResponseLanguage | None,
) -> None:
    if "GENERATOR" not in stages:
        raise ValueError("GROUNDING_GENERATOR_STAGE_MISSING")
    for stage, source in stages.items():
        if source.generating_request_ref.project_id != request_ref.project_id:
            raise ValueError("GROUNDING_STAGE_PROJECT_MISMATCH")
        if source.response_language != language:
            raise ValueError("GROUNDING_STAGE_LANGUAGE_MISMATCH")
        if source.prompt_version != (CONTRACT_V5 if stage == "GENERATOR" else FILLER_V6):
            raise ValueError("GROUNDING_STAGE_CONTRACT_MISMATCH")


class ExpectedByTestV5(DomainModel):
    test_id: str
    rows: tuple[StoredExpectedResultV5, ...] = Field(max_length=10)

    @model_validator(mode="after")
    def unique_targets(self) -> "ExpectedByTestV5":
        if prediction_identity_issues(
            [(self.test_id, tuple(row.hypothesis_id for row in self.rows))]
        ):
            raise ValueError("DUPLICATE_HYPOTHESIS")
        return self


class HypothesisGenerationDetailsV5(HypothesisGenerationDetails):
    contract_version: Literal["hypothesis_portfolio.v5"] = CONTRACT_V5  # pyright: ignore[reportIncompatibleVariableOverride]
    expected_results: tuple[ExpectedByTestV5, ...] = ()  # pyright: ignore[reportIncompatibleVariableOverride]
    generating_request_ref: RevisionRef
    response_language: ResponseLanguage | None
    stage_inputs: dict[GroundingStage, GroundingStageInput]
    grounding_diagnostics: tuple[GroundingRowDiagnostic, ...] = ()

    @model_validator(mode="after")
    def consistent_provenance(self) -> "HypothesisGenerationDetailsV5":
        _validate_stages(self.stage_inputs, self.generating_request_ref, self.response_language)
        tests = [test.test_id for test in self.discriminating_test_candidates]
        if len(tests) != len(set(tests)) or prediction_identity_issues(
            [(test.test_id, ()) for test in self.expected_results]
        ):
            raise ValueError("DUPLICATE_TEST_ID")
        for test in self.expected_results:
            if test.test_id not in tests:
                raise ValueError("GROUNDING_TEST_NOT_IN_CANDIDATES")
            if any(row.source_stage not in self.stage_inputs for row in test.rows):
                raise ValueError("GROUNDING_SOURCE_STAGE_MISSING")
        return self


class PortfolioGenerationDetailsV5(PortfolioGenerationDetails):
    contract_version: Literal["hypothesis_portfolio.v5"] = CONTRACT_V5
    table_fill: TableFillRecordV5 | None = None  # pyright: ignore[reportIncompatibleVariableOverride]
    generating_request_ref: RevisionRef
    response_language: ResponseLanguage | None
    stage_inputs: dict[GroundingStage, GroundingStageInput]
    grounding_diagnostics: tuple[GroundingRowDiagnostic, ...] = ()

    @model_validator(mode="after")
    def consistent_provenance(self) -> "PortfolioGenerationDetailsV5":
        _validate_stages(self.stage_inputs, self.generating_request_ref, self.response_language)
        if self.table_fill is not None:
            if self.table_fill.state == "CALLED" and "FILLER" not in self.stage_inputs:
                raise ValueError("GROUNDING_SOURCE_STAGE_MISSING")
            if self.table_fill.state == "SKIPPED_FULL" and "FILLER" in self.stage_inputs:
                raise ValueError("GROUNDING_UNUSED_FILLER_STAGE")
        return self
