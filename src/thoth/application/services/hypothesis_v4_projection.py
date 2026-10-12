"""Construct v4 canonical details from the accepted request, never a display preference."""

from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisV4
from thoth.domain.hypothesis_v4_projection import (
    ExpectedByTestV4,
    HypothesisGenerationDetailsV4,
    PortfolioGenerationDetailsV4,
)
from thoth.domain.research_execution import research_work
from thoth.domain.research_projection import HypothesisGenerationDetails, PortfolioGenerationDetails


def _origin() -> dict[str, object]:
    work = research_work.get()
    if work is None or work.response_language is None:
        raise ValueError("V4_GENERATING_REQUEST_REQUIRED")
    return {"generating_request_ref": work.request_ref, "response_language": work.response_language}


def hypothesis_details(candidate: Hypothesis, values: dict[str, object]):
    if not isinstance(candidate, HypothesisV4):
        return HypothesisGenerationDetails.model_validate(values)
    return HypothesisGenerationDetailsV4.model_validate(
        {
            **values,
            **_origin(),
            "contract_version": "hypothesis_portfolio.v4",
            "refutation_conditions": candidate.refutation_conditions,
            "dropped_expected_results": tuple(candidate.dropped),
            "expected_results": tuple(
                ExpectedByTestV4(test_id=test.test_id, rows=test.expected_by_hypothesis)
                for test in candidate.discriminating_tests
                if test.expected_by_hypothesis
            ),
        }
    )


def portfolio_details(candidate: HypothesisPortfolio, values: dict[str, object]):
    if not isinstance(candidate, HypothesisPortfolioV4):
        return PortfolioGenerationDetails.model_validate(values)
    return PortfolioGenerationDetailsV4.model_validate({**values, **_origin()})
