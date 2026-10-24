"""Remove ambiguous/invalid v5 groups before the existing language and proposal policies."""

from typing import cast

from thoth.application.services.hypothesis_contract_v3 import settle_contract
from thoth.application.services.hypothesis_grounding_rows import (
    accept_grounding_row,
    row_diagnostic,
)
from thoth.domain.enums import HypothesisStatus
from thoth.domain.hypothesis_grounding_result import GroundedPortfolioResult
from thoth.domain.hypothesis_v5 import (
    DiscriminatingTestV5,
    ExpectedResultV5,
    HypothesisPortfolioV5,
    HypothesisV5,
)
from thoth.domain.hypothesis_v5_identity import prediction_identity_issues
from thoth.domain.hypothesis_v5_wire import ReceivedHypothesisPortfolioV5
from thoth.domain.model import ContextPack
from thoth.domain.prediction_grounding import GroundingRowDiagnostic, GroundingStage
from thoth.domain.response_language import ResponseLanguage


def settle_grounded_portfolio(
    received: ReceivedHypothesisPortfolioV5,
    context: ContextPack,
    language: ResponseLanguage | None,
) -> GroundedPortfolioResult:
    known = {item.hypothesis_id for item in received.hypotheses}
    diagnostics: list[GroundingRowDiagnostic] = []
    hypotheses: list[HypothesisV5] = []
    for owner in received.hypotheses:
        groups = [
            (test.test_id, tuple(row.hypothesis_id for row in test.expected_by_hypothesis))
            for test in owner.discriminating_tests
        ]
        issues = prediction_identity_issues(groups)
        ambiguous_tests = {item.test_index for item in issues if item.row_index is None}
        ambiguous_rows = {(item.test_index, item.row_index) for item in issues}
        tests: list[DiscriminatingTestV5] = []
        for ti, test in enumerate(owner.discriminating_tests):
            if ti in ambiguous_tests:
                diagnostics.extend(
                    row_diagnostic(owner.hypothesis_id, test.test_id, row, "DUPLICATE_TEST_ID")
                    for row in test.expected_by_hypothesis
                )
                if not test.expected_by_hypothesis:
                    diagnostics.append(
                        GroundingRowDiagnostic(
                            owner_hypothesis_id=owner.hypothesis_id,
                            test_id=test.test_id,
                            hypothesis_id="",
                            reason="DUPLICATE_TEST_ID",
                            original_label_chars=0,
                            original_basis_count=0,
                        )
                    )
                continue
            rows: list[ExpectedResultV5] = []
            for ri, row in enumerate(test.expected_by_hypothesis):
                accepted, reason = (
                    (None, "DUPLICATE_HYPOTHESIS")
                    if (ti, ri) in ambiguous_rows
                    else accept_grounding_row(row, known, context, language)
                )
                if accepted is not None:
                    rows.append(accepted)
                else:
                    assert reason is not None
                    diagnostics.append(
                        row_diagnostic(owner.hypothesis_id, test.test_id, row, reason)
                    )
            tests.append(
                DiscriminatingTestV5.model_validate(
                    {
                        **test.model_dump(exclude={"expected_by_hypothesis"}),
                        "expected_by_hypothesis": rows,
                    }
                )
            )
        data = owner.model_dump(exclude={"discriminating_tests"})
        # A model's incomplete candidate cannot retain a promoted stage after all its tests drop.
        if not tests:
            data["status"] = HypothesisStatus.DRAFT
        hypotheses.append(HypothesisV5.model_validate({**data, "discriminating_tests": tests}))
    final = HypothesisPortfolioV5.model_validate(
        {
            **received.model_dump(exclude={"hypotheses"}),
            "hypotheses": hypotheses,
        }
    )
    final = cast(
        HypothesisPortfolioV5,
        settle_contract(
            final,
            context.evidence,
            context.problem,
            context=context,
        ),
    )
    origins: dict[tuple[str, str, str], GroundingStage] = {
        (owner.hypothesis_id, test.test_id, row.hypothesis_id): "GENERATOR"
        for owner in final.hypotheses
        for test in owner.discriminating_tests
        for row in test.expected_by_hypothesis
    }
    return GroundedPortfolioResult(final, tuple(diagnostics), origins)
