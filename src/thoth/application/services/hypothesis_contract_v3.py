"""Hypothesis generation contract v3 (off unless the project turns it on).

The same single generator call is made either way; only the form it is shown and the contract text
change. In v3 each discriminating test may say what every hypothesis expects from it (a short label,
"모름" when not known) and each hypothesis may say what result would make it give the hypothesis up.
What the model gets wrong about ids is dropped item by item and recorded, never failing the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import (
    DiscriminatingTest,
    DiscriminatingTestV3,
    ExpectedResult,
    Hypothesis,
    HypothesisPortfolio,
    HypothesisPortfolioV3,
    HypothesisV3,
)
from thoth.domain.research_projection import DroppedExpectation, ExpectedByTest

CONTRACT_V2 = "hypothesis_portfolio.v2"
CONTRACT_V3 = "hypothesis_portfolio.v3"


class V3DetailFields(TypedDict, total=False):
    refutation_conditions: tuple[str, ...]
    expected_results: tuple[ExpectedByTest, ...]
    dropped_expected_results: tuple[DroppedExpectation, ...]
    contract_version: str


class GeneratorContract(TypedDict):
    output_model: type[HypothesisPortfolio]
    prompt_version: str


def generator_contract(v3: bool) -> GeneratorContract:
    """The form and contract version for the generator call; v2 exactly as it always was."""
    if v3:
        return {"output_model": HypothesisPortfolioV3, "prompt_version": CONTRACT_V3}
    return {"output_model": HypothesisPortfolio, "prompt_version": CONTRACT_V2}


def base_tests(candidate: Hypothesis) -> tuple[DiscriminatingTest, ...]:
    """The tests as the v2 record keeps them; a v3 test's extra fields are kept apart."""
    if not isinstance(candidate, HypothesisV3):
        return candidate.discriminating_tests
    return tuple(
        DiscriminatingTest.model_validate(item.model_dump(exclude={"expected_by_hypothesis"}))
        for item in candidate.discriminating_tests
    )


def v3_details(candidate: Hypothesis) -> V3DetailFields:
    """The generation-details fields a v3 hypothesis adds; a v2 hypothesis adds none."""
    if not isinstance(candidate, HypothesisV3):
        return {}
    return {
        "refutation_conditions": candidate.refutation_conditions,
        "expected_results": tuple(
            ExpectedByTest(test_id=item.test_id, rows=item.expected_by_hypothesis)
            for item in candidate.discriminating_tests
            if item.expected_by_hypothesis
        ),
        "dropped_expected_results": tuple(candidate.dropped),
        "contract_version": CONTRACT_V3,
    }


@dataclass(frozen=True)
class _Settled:
    hypothesis: HypothesisV3
    dropped: tuple[DroppedExpectation, ...]


def _settle_one(
    hypothesis: HypothesisV3, known_hypotheses: set[str], known_spans: set[str]
) -> _Settled:
    dropped: list[DroppedExpectation] = []
    tests: list[DiscriminatingTestV3] = []
    for test in hypothesis.discriminating_tests:
        kept: list[ExpectedResult] = []
        for row in test.expected_by_hypothesis:
            reason = (
                "UNKNOWN_HYPOTHESIS"
                if row.hypothesis_id not in known_hypotheses
                else "DUPLICATE_HYPOTHESIS"
                if any(item.hypothesis_id == row.hypothesis_id for item in kept)
                else "UNKNOWN_SPAN"
                if not set(row.basis) <= known_spans
                else None
            )
            if reason is None:
                kept.append(row)
            else:
                dropped.append(
                    DroppedExpectation(
                        test_id=test.test_id, hypothesis_id=row.hypothesis_id, reason=reason
                    )
                )
        tests.append(test.model_copy(update={"expected_by_hypothesis": tuple(kept)}))
    settled = hypothesis.model_copy(update={"discriminating_tests": tuple(tests)})
    return _Settled(settled, tuple(dropped))


def settle_contract(
    portfolio: HypothesisPortfolio, evidence: tuple[EvidenceSpan, ...]
) -> HypothesisPortfolio:
    """Keep the rows of a v3 output that name known hypotheses and spans; v2 is untouched."""
    if not isinstance(portfolio, HypothesisPortfolioV3):
        return portfolio
    known_hypotheses = {item.hypothesis_id for item in portfolio.hypotheses}
    known_spans = {span.span_id for span in evidence}
    settled: list[HypothesisV3] = []
    for hypothesis in portfolio.hypotheses:
        found = _settle_one(hypothesis, known_hypotheses, known_spans)
        found.hypothesis.note_dropped(found.dropped)
        settled.append(found.hypothesis)
    return portfolio.model_copy(update={"hypotheses": tuple(settled)})
