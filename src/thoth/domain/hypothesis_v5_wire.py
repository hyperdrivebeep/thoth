"""Model receive shapes: cross-field grounding and ambiguity checks happen per row later."""

from pydantic import Field, SerializeAsAny

from thoth.domain.base import DomainModel
from thoth.domain.hypothesis_v4 import (
    DiscriminatingTestV4,
    ExpectedResultV4,
    FilledRowV4,
    FilledTestV4,
    HypothesisPortfolioV4,
    HypothesisV4,
)
from thoth.domain.prediction_grounding import DerivationRole


class ReceivedGroundingV1(DomainModel):
    schema_version: str = "1.0.0"
    derivation_role: DerivationRole
    rationale: str = Field(max_length=200)


class ReceivedExpectedResultV5(ExpectedResultV4):
    grounding: ReceivedGroundingV1


class ReceivedDiscriminatingTestV5(DiscriminatingTestV4):
    expected_by_hypothesis: tuple[ReceivedExpectedResultV5, ...] = Field(default=(), max_length=10)  # pyright: ignore[reportIncompatibleVariableOverride]


class ReceivedHypothesisV5(HypothesisV4):
    discriminating_tests: tuple[SerializeAsAny[ReceivedDiscriminatingTestV5], ...]  # pyright: ignore[reportIncompatibleVariableOverride]


class ReceivedHypothesisPortfolioV5(HypothesisPortfolioV4):
    hypotheses: tuple[SerializeAsAny[ReceivedHypothesisV5], ...]  # pyright: ignore[reportIncompatibleVariableOverride]


class ReceivedFilledRowV5(FilledRowV4):
    grounding: ReceivedGroundingV1


class ReceivedFilledTestV5(FilledTestV4):
    rows: tuple[ReceivedFilledRowV5, ...] = Field(default=(), max_length=40)  # pyright: ignore[reportIncompatibleVariableOverride]


class ReceivedHypothesisTableFillV5(DomainModel):
    tests: tuple[ReceivedFilledTestV5, ...] = Field(default=(), max_length=60)
