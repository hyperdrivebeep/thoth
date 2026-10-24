"""Construct v5 details from captured calls; restore views without rewriting storage."""

from thoth.application.services.hypothesis_grounding_bindings import (
    bind_grounded_row,
    grounding_work,
)
from thoth.domain.hypothesis_v5 import HypothesisPortfolioV5, HypothesisV5
from thoth.domain.hypothesis_v5_projection import (
    ExpectedByTestV5,
    HypothesisGenerationDetailsV5,
    PortfolioGenerationDetailsV5,
)


def hypothesis_details_v5(candidate: HypothesisV5, values: dict[str, object]):
    work = grounding_work()
    assert work.hypothesis_grounding is not None
    return HypothesisGenerationDetailsV5.model_validate(
        {
            **values,
            "contract_version": "hypothesis_portfolio.v5",
            "generating_request_ref": work.request_ref,
            "response_language": work.response_language,
            "stage_inputs": {name: item.metadata for name, item in work.grounding_stages.items()},
            "refutation_conditions": candidate.refutation_conditions,
            "dropped_expected_results": tuple(candidate.dropped),
            "grounding_diagnostics": tuple(
                d
                for d in work.hypothesis_grounding.diagnostics
                if d.owner_hypothesis_id == candidate.hypothesis_id
            ),
            "expected_results": tuple(
                ExpectedByTestV5(
                    test_id=test.test_id,
                    rows=tuple(
                        bind_grounded_row(candidate.hypothesis_id, test.test_id, row, work)
                        for row in test.expected_by_hypothesis
                    ),
                )
                for test in candidate.discriminating_tests
                if test.expected_by_hypothesis
            ),
        }
    )


def portfolio_details_v5(candidate: HypothesisPortfolioV5, values: dict[str, object]):
    work = grounding_work()
    assert work.hypothesis_grounding is not None
    owners = {item.hypothesis_id for item in candidate.hypotheses}
    return PortfolioGenerationDetailsV5.model_validate(
        {
            **values,
            "generating_request_ref": work.request_ref,
            "response_language": work.response_language,
            "stage_inputs": {name: item.metadata for name, item in work.grounding_stages.items()},
            "grounding_diagnostics": tuple(
                d
                for d in work.hypothesis_grounding.diagnostics
                if d.owner_hypothesis_id not in owners
            ),
        }
    )


def hypothesis_view_v5(
    fields: dict[str, object], details: HypothesisGenerationDetailsV5
) -> HypothesisV5:
    by_test = {group.test_id: group.rows for group in details.expected_results}
    tests = [
        {
            **test.model_dump(),
            "expected_by_hypothesis": [
                row.model_dump(exclude={"source_stage", "source_bindings", "relation_assessment"})
                for row in by_test.get(test.test_id, ())
            ],
        }
        for test in details.discriminating_test_candidates
    ]
    result = HypothesisV5.model_validate(
        {
            **fields,
            "discriminating_tests": tests,
            "refutation_conditions": details.refutation_conditions,
        }
    )
    result.note_dropped(details.dropped_expected_results)
    return result
