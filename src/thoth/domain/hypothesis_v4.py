"""Language-independent expected-result states; v2/v3 codecs remain unchanged."""

from typing import Literal

from pydantic import Field, SerializeAsAny, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.hypothesis import (
    DiscriminatingTestV3,
    HypothesisPortfolioV3,
    HypothesisV3,
    TableFillAnswer,
    TableFillRecord,
)
from thoth.domain.ids import EvidenceSpanId, HypothesisId

CONTRACT_V4 = "hypothesis_portfolio.v4"
FILLER_V5 = "hypothesis_table_filler.v5"


class ExpectedResultV4(DomainModel):
    hypothesis_id: HypothesisId
    knowledge_state: Literal["PREDICTED", "UNKNOWN"]
    expected: str | None = Field(min_length=1, max_length=80)
    basis: tuple[EvidenceSpanId, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def consistent_state(self) -> "ExpectedResultV4":
        if (self.knowledge_state == "UNKNOWN") != (self.expected is None):
            raise ValueError("EXPECTED_RESULT_STATE_CONFLICT")
        if self.expected is not None and not self.expected.strip():
            raise ValueError("EXPECTED_RESULT_TEXT_EMPTY")
        return self


class DiscriminatingTestV4(DiscriminatingTestV3):
    expected_by_hypothesis: tuple[ExpectedResultV4, ...] = Field(default=(), max_length=10)  # pyright: ignore[reportIncompatibleVariableOverride]


class HypothesisV4(HypothesisV3):
    discriminating_tests: tuple[SerializeAsAny[DiscriminatingTestV4], ...]  # pyright: ignore[reportIncompatibleVariableOverride]


class HypothesisPortfolioV4(HypothesisPortfolioV3):
    hypotheses: tuple[SerializeAsAny[HypothesisV4], ...]  # pyright: ignore[reportIncompatibleVariableOverride]


class FilledRowV4(ExpectedResultV4):
    expected: str | None = Field(min_length=1, max_length=300)
    basis: tuple[EvidenceSpanId, ...] = Field(default=(), max_length=20)
    unknown_reason: str | None = Field(default=None, max_length=2000)


class FilledTestV4(DomainModel):
    designed_for: HypothesisId
    test_id: str = Field(min_length=1, max_length=300)
    rows: tuple[FilledRowV4, ...] = Field(default=(), max_length=40)


class HypothesisTableFillV4(DomainModel):
    tests: tuple[FilledTestV4, ...] = Field(default=(), max_length=60)


class TableFillAnswerV4(TableFillAnswer):
    expected: str | None = Field(default=None, max_length=80)  # pyright: ignore[reportIncompatibleVariableOverride]
    knowledge_state: Literal["PREDICTED", "UNKNOWN"] | None = None


class TableFillRecordV4(TableFillRecord):
    contract_version: Literal["hypothesis_table_filler.v5"] = FILLER_V5
    answers: tuple[TableFillAnswerV4, ...] = ()  # pyright: ignore[reportIncompatibleVariableOverride]


def expected_is_unknown(row: object) -> bool:
    if isinstance(row, (ExpectedResultV4, FilledRowV4)):
        return row.knowledge_state == "UNKNOWN"
    # Read projection only: preserve the server's historical Unicode whitespace semantics.
    return " ".join(str(getattr(row, "expected", "")).split()) == "모름"
