"""A second, narrow call that fills the expected-result cells the generator left open (v3 only).

The generator writes the hypotheses, the tests, the tables and the refutation conditions in one
call and tends to pass quickly over the cells of hypotheses a test was not designed for. After the
output is settled this call is asked for those cells only. The merge is decided here, not by the
model: a designed-for cell is the generator's own and stays; a cell the generator filled is not
overwritten; only a cell that is missing or says 모름 may change. If the call cannot be made or its
answer cannot be used, the table stays as the generator wrote it and the reason is recorded; the
research never fails because of this call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from thoth.application.reducers import validate_hypothesis_portfolio
from thoth.application.services.english_prose import EnglishProseCheck, record_english_check
from thoth.application.services.hypothesis_contract_v3 import language_problem, settle_contract
from thoth.application.services.request_language import validation_problem
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import (
    DiscriminatingTestV3,
    ExpectedResult,
    FilledRow,
    HypothesisPortfolio,
    HypothesisPortfolioV3,
    HypothesisTableFill,
    HypothesisV3,
    TableFillAnswer,
    TableFillOutcome,
    TableFillRecord,
)
from thoth.domain.hypothesis_v4 import (
    FILLER_V5,
    ExpectedResultV4,
    FilledRowV4,
    HypothesisPortfolioV4,
    HypothesisTableFillV4,
    TableFillAnswerV4,
    TableFillRecordV4,
    expected_is_unknown,
)
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.research_execution import research_work
from thoth.domain.research_projection import DroppedExpectation
from thoth.ports.model import ModelExecutionHold, ModelPort

TABLE_FILLER_VERSION = "hypothesis_table_filler.v4"
# Only the open cells are asked for (about fifty tokens a row), so this is below the generator's
# 4,000 output tokens; it is not raised to make room.
TABLE_FILLER_MAX_OUTPUT_TOKENS = 3_000
UNKNOWN = "모름"
MAX_LABEL_CHARS = 80
MAX_BASIS = 5
MAX_ROWS = 10
# A hold that says the research's own limits are used up: the call is skipped, not failed.
BUDGET_HOLDS = frozenset({"SERIALIZED_MODEL_INPUT_LIMIT", "RESEARCH_BUDGET_MISSING"})
UNUSABLE_ANSWER = "TABLE_FILL_OUTPUT_INVALID"


class CycleInput(Protocol):
    @property
    def cutoff_at(self) -> datetime: ...
    @property
    def model_policy_ref(self) -> str: ...
    @property
    def evidence(self) -> tuple[EvidenceSpan, ...]: ...


def _label(value: str | None) -> str:
    return "" if value is None else " ".join(value.split())


def _rows_of(test: DiscriminatingTestV3) -> dict[str, str | None]:
    return {
        row.hypothesis_id: None if expected_is_unknown(row) else _label(row.expected)
        for row in test.expected_by_hypothesis
    }


def _emptied_by_language(owner: HypothesisV3, test: DiscriminatingTestV3) -> bool:
    """True when settling dropped the designed-for cell's own label for its language or script."""
    return any(
        item.test_id == test.test_id
        and item.hypothesis_id == owner.hypothesis_id
        and item.reason.startswith("EXPECTED_")
        and item.reason.endswith("_MISMATCH")
        for item in owner.dropped
    )


def _open_ids(
    portfolio: HypothesisPortfolioV3, owner: HypothesisV3, test: DiscriminatingTestV3
) -> tuple[str, ...]:
    """The hypotheses whose cell is missing or says 모름, other than the one the test is for.

    The designed-for cell is the generator's own and stays; it is open only when settling dropped
    its label for its language or script: the cell is empty only because of the label's wording.
    """
    rows = _rows_of(test)
    own_open = owner.hypothesis_id not in rows and _emptied_by_language(owner, test)
    return tuple(
        item.hypothesis_id
        for item in portfolio.hypotheses
        if (item.hypothesis_id != owner.hypothesis_id or own_open)
        and rows.get(item.hypothesis_id) is None
    )


def count_cells(portfolio: HypothesisPortfolioV3) -> tuple[int, int]:
    """(cells, open cells) over every test and every hypothesis, the designed-for cell included."""
    cells = opened = 0
    for owner in portfolio.hypotheses:
        for test in owner.discriminating_tests:
            rows = _rows_of(test)
            for item in portfolio.hypotheses:
                cells += 1
                opened += rows.get(item.hypothesis_id) is None
    return cells, opened


def _payload(portfolio: HypothesisPortfolioV3) -> dict[str, object]:
    return {
        "hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "statement": item.statement,
                "predicted_observations": list(item.predicted_observations),
            }
            for item in portfolio.hypotheses
        ],
        "tests": [
            {
                "designed_for": owner.hypothesis_id,
                "test_id": test.test_id,
                "procedure": test.procedure_candidate,
                "expected_if_true": test.expected_if_true,
                "expected_if_alternative": test.expected_if_alternative,
                "table": [
                    {**row.model_dump(exclude={"basis"})} for row in test.expected_by_hypothesis
                ],
                "open_hypothesis_ids": list(open_ids),
            }
            for owner in portfolio.hypotheses
            for test in owner.discriminating_tests
            if (open_ids := _open_ids(portfolio, owner, test))
        ],
    }


def _request_context(context: ContextPack, portfolio: HypothesisPortfolioV3) -> ContextPack:
    """The same question and evidence as the generator saw, and only the tables to fill."""
    return ContextPack(
        case_id=context.case_id,
        project_id=context.project_id,
        object_id=context.object_id,
        problem=context.problem,
        evidence=context.evidence,
        criteria=(),
        sufficiency=None,
        input_head_set_digest=context.input_head_set_digest,
        research_context={
            "task": "Fill the open cells of the expected-result tables; change nothing else.",
            "table_filler": _payload(portfolio),
        },
    )


def _row_problem(
    row: FilledRow | FilledRowV4, open_ids: set[str], known: set[str], problem: str
) -> str | None:
    """Why this answer for one cell is not kept, or None when it is."""
    label = _label(row.expected)
    if row.hypothesis_id not in known:
        return "UNKNOWN_HYPOTHESIS"
    if row.hypothesis_id not in open_ids:
        return "TABLE_FILL_CELL_NOT_OPEN"
    if expected_is_unknown(row):
        return None
    if len(label) > MAX_LABEL_CHARS:
        return "EXPECTED_TOO_LONG"
    work = research_work.get()
    if work is not None and work.response_language == "en":
        return None  # English fields are audited below, with original question/source quotes.
    mismatch = language_problem(label, validation_problem(problem))
    return None if mismatch is None else f"EXPECTED_{mismatch}_MISMATCH"


def merge_fill(
    portfolio: HypothesisPortfolioV3,
    fill: HypothesisTableFill | HypothesisTableFillV4,
    known_spans: set[str],
    problem: str,
    *,
    context: ContextPack | None = None,
) -> tuple[HypothesisPortfolioV3, int, tuple[TableFillAnswer, ...]]:
    """The portfolio with the answered cells merged in, how many cells changed to a value, and
    what the filler said for each cell it was asked about.

    Every answer that is not kept is recorded on the hypothesis the test was designed for.
    """
    known = {item.hypothesis_id for item in portfolio.hypotheses}
    work = research_work.get()
    english = (
        EnglishProseCheck.build(
            problem,
            () if context is None else context.evidence,
            (*known, *known_spans, portfolio.object_id),
            context,
        )
        if work is not None and work.response_language == "en"
        else None
    )
    position = {item.hypothesis_id: index for index, item in enumerate(portfolio.hypotheses)}
    tests = {
        (owner.hypothesis_id, test.test_id): (owner, test, set(_open_ids(portfolio, owner, test)))
        for owner in portfolio.hypotheses
        for test in owner.discriminating_tests
    }
    kept: dict[tuple[str, str], dict[str, ExpectedResult | ExpectedResultV4]] = {}
    said: dict[tuple[str, str, str], TableFillAnswer] = {}
    dropped: dict[str, list[DroppedExpectation]] = {}

    def drop(owner_id: str, test_id: str, hypothesis_id: str, reason: str, text: str = "") -> None:
        owner = owner_id if owner_id in known else portfolio.hypotheses[0].hypothesis_id
        dropped.setdefault(owner, []).append(
            DroppedExpectation(
                test_id=test_id, hypothesis_id=hypothesis_id, reason=reason, text=text or None
            )
        )

    def note(
        cell: tuple[str, str, str], row: FilledRow | FilledRowV4, outcome: TableFillOutcome
    ) -> None:
        """What was said for an asked cell; a kept answer replaces a dropped one only."""
        before = said.get(cell)
        if before is not None and not (before.outcome == "DROPPED" and outcome != "DROPPED"):
            return
        label = _label(row.expected)
        reason = (row.unknown_reason or "").strip()[:200] if expected_is_unknown(row) else ""
        codec = TableFillAnswerV4 if isinstance(row, FilledRowV4) else TableFillAnswer
        extra = {"knowledge_state": row.knowledge_state} if isinstance(row, FilledRowV4) else {}
        said[cell] = codec.model_validate(
            {
                **extra,
                "test_id": cell[1],
                "hypothesis_id": cell[2],
                "expected": None
                if isinstance(row, FilledRowV4) and expected_is_unknown(row)
                else label[:MAX_LABEL_CHARS],
                "unknown_reason": reason or None,
                "outcome": outcome,
            }
        )

    for item in fill.tests:
        key = (item.designed_for, item.test_id)
        if key not in tests:
            drop(item.designed_for, item.test_id, "", "UNKNOWN_TEST")
            continue
        open_ids = tests[key][2]
        answers = kept.setdefault(key, {})
        for row in item.rows:
            cell = (item.designed_for, item.test_id, row.hypothesis_id)
            reason = _row_problem(row, open_ids, known, problem)
            if reason is None and row.hypothesis_id in answers:
                reason = "DUPLICATE_HYPOTHESIS"
            if reason is None and not set(row.basis) <= known_spans:
                reason = "UNKNOWN_SPAN"
            if reason is not None:
                text = row.expected if reason.startswith("EXPECTED_") else ""
                drop(item.designed_for, item.test_id, row.hypothesis_id, reason, text or "")
                if row.hypothesis_id in open_ids:
                    note(cell, row, "DROPPED")
                continue
            if english is not None:
                for field, text in (
                    ("EXPECTED", row.expected),
                    ("TABLE_FILL_REASON", row.unknown_reason),
                ):
                    if english.mismatch(text):
                        drop(
                            item.designed_for,
                            item.test_id,
                            row.hypothesis_id,
                            f"{field}_LANGUAGE_FLAGGED",
                            text or "",
                        )
            codec = ExpectedResultV4 if isinstance(row, FilledRowV4) else ExpectedResult
            extra = {"knowledge_state": row.knowledge_state} if isinstance(row, FilledRowV4) else {}
            answers[row.hypothesis_id] = codec.model_validate(
                {
                    **extra,
                    "hypothesis_id": row.hypothesis_id,
                    "expected": None
                    if isinstance(row, FilledRowV4) and expected_is_unknown(row)
                    else _label(row.expected),
                    "basis": row.basis[:MAX_BASIS],
                }
            )
            note(cell, row, "STILL_UNKNOWN" if expected_is_unknown(row) else "CHANGED")
    changed = 0
    settled: list[HypothesisV3] = []
    for owner in portfolio.hypotheses:
        new_tests: list[DiscriminatingTestV3] = []
        for test in owner.discriminating_tests:
            answers = kept.get((owner.hypothesis_id, test.test_id), {})
            rows: list[ExpectedResult | ExpectedResultV4] = list(test.expected_by_hypothesis)
            at = {row.hypothesis_id: index for index, row in enumerate(rows)}
            for hypothesis_id in sorted(answers, key=position.__getitem__):
                answer = answers[hypothesis_id]
                if hypothesis_id in at:
                    if not expected_is_unknown(answer):
                        rows[at[hypothesis_id]] = answer
                        changed += 1
                elif len(rows) < MAX_ROWS:
                    rows.append(answer)
                    changed += not expected_is_unknown(answer)
                else:
                    drop(owner.hypothesis_id, test.test_id, hypothesis_id, "TABLE_ROW_LIMIT")
                    cell = (owner.hypothesis_id, test.test_id, hypothesis_id)
                    said[cell] = said[cell].model_copy(update={"outcome": "DROPPED"})
            new_tests.append(test.model_copy(update={"expected_by_hypothesis": tuple(rows)}))
        changed_owner = owner.model_copy(update={"discriminating_tests": tuple(new_tests)})
        changed_owner.note_dropped((*owner.dropped, *dropped.get(owner.hypothesis_id, ())))
        settled.append(changed_owner)
    return (
        portfolio.model_copy(update={"hypotheses": tuple(settled)}),
        changed,
        _answers(portfolio, tests, said),
    )


def _answers(
    portfolio: HypothesisPortfolioV3,
    tests: dict[tuple[str, str], tuple[HypothesisV3, DiscriminatingTestV3, set[str]]],
    said: dict[tuple[str, str, str], TableFillAnswer],
) -> tuple[TableFillAnswer, ...]:
    """One entry for every cell the filler was asked about, in the portfolio's order; a cell it
    gave no row for is NOT_ANSWERED."""
    found: list[TableFillAnswer] = []
    for (owner_id, test_id), (_, _, open_ids) in tests.items():
        for item in portfolio.hypotheses:
            if item.hypothesis_id in open_ids:
                found.append(
                    said.get((owner_id, test_id, item.hypothesis_id))
                    or (
                        TableFillAnswerV4
                        if isinstance(portfolio, HypothesisPortfolioV4)
                        else TableFillAnswer
                    )(test_id=test_id, hypothesis_id=item.hypothesis_id, outcome="NOT_ANSWERED")
                )
    return tuple(found)


def _noted(portfolio: HypothesisPortfolioV3, record: TableFillRecord) -> HypothesisPortfolioV3:
    result = portfolio.model_copy()
    result.note_table_fill(record)
    work = research_work.get()
    if work is not None:
        work.result_only["hypothesis_table_fill"] = record.model_dump(mode="json")
    return result


async def fill_tables(
    model: ModelPort,
    portfolio: HypothesisPortfolio,
    context: ContextPack,
    command: CycleInput,
) -> HypothesisPortfolio:
    """The portfolio with its open cells filled, when it is a v3 portfolio that has any."""
    if not isinstance(portfolio, HypothesisPortfolioV3) or len(portfolio.hypotheses) < 2:
        return portfolio
    cells, open_before = count_cells(portfolio)
    if cells == 0:
        return portfolio
    record_codec = (
        TableFillRecordV4 if isinstance(portfolio, HypothesisPortfolioV4) else TableFillRecord
    )
    base = record_codec(state="SKIPPED_FULL", cells=cells, open_before=open_before)
    if not _payload(portfolio)["tests"]:
        return _noted(portfolio, base.model_copy(update={"open_after": open_before}))
    try:
        result = await model.structured(
            ModelRequest(
                role=ModelRole.HYPOTHESIS_TABLE_FILLER,
                project_id=context.project_id,
                cutoff_at=command.cutoff_at,
                context_pack=_request_context(context, portfolio),
                output_model=HypothesisTableFillV4
                if isinstance(portfolio, HypothesisPortfolioV4)
                else HypothesisTableFill,
                prompt_version=FILLER_V5
                if isinstance(portfolio, HypothesisPortfolioV4)
                else TABLE_FILLER_VERSION,
                model_policy_ref=command.model_policy_ref,
                max_output_tokens=TABLE_FILLER_MAX_OUTPUT_TOKENS,
            )
        )
    except (ModelExecutionHold, ValidationError) as error:
        reason = UNUSABLE_ANSWER if isinstance(error, ValidationError) else str(error)
        state = "SKIPPED_BUDGET" if reason in BUDGET_HOLDS else "FAILED"
        failed = base.model_copy(
            update={"state": state, "reason_code": reason[:200], "open_after": open_before}
        )
        return _noted(portfolio, failed)
    merged, changed, answers = merge_fill(
        portfolio,
        result.output,
        {span.span_id for span in command.evidence},
        context.problem,
        context=context,
    )
    _, open_after = count_cells(merged)
    called = base.model_copy(
        update={
            "state": "CALLED",
            "open_after": open_after,
            "changed_cells": changed,
            "answers": answers,
        }
    )
    return _noted(merged, called)


async def accepted_portfolio(
    model: ModelPort,
    generated: HypothesisPortfolio,
    context: ContextPack,
    command: CycleInput,
    require_unknown: bool,
) -> HypothesisPortfolio:
    """The generator's output settled, its open table cells filled (v3), and validated."""
    settled = settle_contract(generated, command.evidence, context.problem, context=context)
    filled = await fill_tables(model, settled, context, command)
    accepted = validate_hypothesis_portfolio(
        filled, evidence=command.evidence, require_unknown_alternative=require_unknown
    )
    record_english_check(accepted, context)
    return accepted
