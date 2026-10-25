"""The existing open-cell policy with v5 grounding retained and ambiguous replies excluded."""

from collections import Counter
from dataclasses import replace
from typing import cast

from thoth.application.services.hypothesis_contract_v3 import language_problem
from thoth.application.services.hypothesis_grounding_rows import (
    accept_grounding_row,
    row_diagnostic,
)
from thoth.application.services.hypothesis_table_filler import (
    open_ids as _open_ids,
)
from thoth.application.services.hypothesis_table_filler import (
    request_context as _request_context,
)
from thoth.domain.hypothesis_grounding_result import GroundedPortfolioResult, PredictionCell
from thoth.domain.hypothesis_v4 import TableFillAnswerV4, expected_is_unknown
from thoth.domain.hypothesis_v5 import DiscriminatingTestV5, ExpectedResultV5, HypothesisV5
from thoth.domain.hypothesis_v5_wire import ReceivedHypothesisTableFillV5
from thoth.domain.model import ContextPack
from thoth.domain.prediction_grounding import GroundingRowDiagnostic
from thoth.domain.response_language import ResponseLanguage


def grounding_fill_context(context: ContextPack, result: GroundedPortfolioResult) -> ContextPack:
    filled = _request_context(context, result.portfolio)
    payload = cast(dict[str, object], filled.research_context["table_filler"])
    for test in cast(list[dict[str, object]], payload["tests"]):
        test["table"] = [
            {key: row[key] for key in ("hypothesis_id", "knowledge_state", "expected")}
            for row in cast(list[dict[str, object]], test["table"])
        ]
    return filled


def merge_grounded_fill(
    initial: GroundedPortfolioResult,
    reply: ReceivedHypothesisTableFillV5,
    context: ContextPack,
    language: ResponseLanguage | None,
) -> tuple[GroundedPortfolioResult, int, tuple[TableFillAnswerV4, ...]]:
    portfolio = initial.portfolio
    known = {item.hypothesis_id for item in portfolio.hypotheses}
    tests = {
        (owner.hypothesis_id, test.test_id): (owner, test, set(_open_ids(portfolio, owner, test)))
        for owner in portfolio.hypotheses
        for test in owner.discriminating_tests
    }
    groups = Counter((item.designed_for, item.test_id) for item in reply.tests)
    accepted: dict[PredictionCell, ExpectedResultV5] = {}
    said: dict[PredictionCell, TableFillAnswerV4] = {}
    diagnostics = list(initial.diagnostics)
    for item in reply.tests:
        key = (item.designed_for, item.test_id)
        targets = Counter(row.hypothesis_id for row in item.rows)
        for row in item.rows:
            cell = (*key, row.hypothesis_id)
            reason = (
                "UNKNOWN_TEST"
                if key not in tests
                else "DUPLICATE_TEST_ID"
                if groups[key] > 1
                else "DUPLICATE_HYPOTHESIS"
                if targets[row.hypothesis_id] > 1
                else "TABLE_FILL_CELL_NOT_OPEN"
                if row.hypothesis_id not in tests[key][2]
                else None
            )
            kept = None
            if reason is None:
                kept, reason = accept_grounding_row(
                    row,
                    known,
                    context,
                    language,
                    normalize_label=True,
                )
            if kept is not None and not expected_is_unknown(kept) and language != "en":
                problem = "한국어 " + context.problem if language == "ko" else context.problem
                mismatch = language_problem(kept.expected or "", problem)
                if mismatch:
                    kept, reason = None, f"EXPECTED_{mismatch}_MISMATCH"
            if reason is not None:
                diagnostics.append(row_diagnostic(item.designed_for, item.test_id, row, reason))
                if key in tests and row.hypothesis_id in tests[key][2]:
                    said[cell] = TableFillAnswerV4(
                        test_id=item.test_id,
                        hypothesis_id=row.hypothesis_id,
                        outcome="DROPPED",
                    )
                continue
            assert kept is not None
            accepted[cell] = kept
            said[cell] = TableFillAnswerV4(
                test_id=item.test_id,
                hypothesis_id=row.hypothesis_id,
                expected=kept.expected,
                knowledge_state=kept.knowledge_state,
                outcome="STILL_UNKNOWN" if expected_is_unknown(kept) else "CHANGED",
                unknown_reason=(row.unknown_reason or "").strip()[:200]
                if expected_is_unknown(kept)
                else None,
            )
    changed = 0
    origins = dict(initial.row_stages)
    owners: list[HypothesisV5] = []
    for owner in portfolio.hypotheses:
        rebuilt: list[DiscriminatingTestV5] = []
        for test in owner.discriminating_tests:
            rows = list(test.expected_by_hypothesis)
            positions = {row.hypothesis_id: i for i, row in enumerate(rows)}
            for target in portfolio.hypotheses:
                cell = (owner.hypothesis_id, test.test_id, target.hypothesis_id)
                row = accepted.get(cell)
                if row is None:
                    continue
                if target.hypothesis_id in positions:
                    if expected_is_unknown(row):
                        continue
                    rows[positions[target.hypothesis_id]] = row
                elif len(rows) < 10:
                    rows.append(row)
                else:
                    diagnostics.append(
                        GroundingRowDiagnostic(
                            owner_hypothesis_id=owner.hypothesis_id,
                            test_id=test.test_id,
                            hypothesis_id=target.hypothesis_id,
                            reason="TABLE_ROW_LIMIT",
                            original_label_chars=len(row.expected or ""),
                            original_basis_count=len(row.basis),
                        )
                    )
                    said[cell] = said[cell].model_copy(update={"outcome": "DROPPED"})
                    continue
                origins[cell] = "FILLER"
                changed += not expected_is_unknown(row)
            rebuilt.append(test.model_copy(update={"expected_by_hypothesis": tuple(rows)}))
        owners.append(owner.model_copy(update={"discriminating_tests": tuple(rebuilt)}))
    answers = tuple(
        said.get((owner_id, test_id, target.hypothesis_id))
        or TableFillAnswerV4(
            test_id=test_id, hypothesis_id=target.hypothesis_id, outcome="NOT_ANSWERED"
        )
        for (owner_id, test_id), (_, _, open_ids) in tests.items()
        for target in portfolio.hypotheses
        if target.hypothesis_id in open_ids
    )
    return (
        replace(
            initial,
            portfolio=portfolio.model_copy(update={"hypotheses": tuple(owners)}),
            diagnostics=tuple(diagnostics),
            row_stages=origins,
        ),
        changed,
        answers,
    )
