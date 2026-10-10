"""The table-filling call: when it is made, what it may change, and what happens when it fails."""

from __future__ import annotations

from asyncio import CancelledError
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel
from tests.unit.test_research_short_text_selection import source

from thoth.application.services.hypothesis_table_filler import (
    TABLE_FILLER_MAX_OUTPUT_TOKENS,
    TABLE_FILLER_VERSION,
    accepted_portfolio,
    count_cells,
    fill_tables,
    merge_fill,
)
from thoth.domain.canonical import model_digest
from thoth.domain.enums import (
    CausalDepth,
    HypothesisStatus,
    ModelRole,
    PortfolioStatus,
    Reversibility,
    RiskTier,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import (
    ExpectedResult,
    FilledRow,
    FilledTest,
    HypothesisPortfolio,
    HypothesisPortfolioV3,
    HypothesisTableFill,
)
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.research_execution import ResearchFence
from thoth.domain.research_projection import DroppedExpectation
from thoth.ports.model import ModelExecutionHold

HEAD = "a" * 64
KOREAN = '「SYN-C-DET-RAIN」 기준의 판정이 "미달"입니다. 원인 후보를 찾아 주세요.'
IDS = ("h1", "h2", "h3")
SAME = "같은 결과가 남는다"
WORSE = "탐지가 더 나빠진다"


def hypothesis(owner: str, table: dict[str, str]) -> dict[str, Any]:
    return {
        "hypothesis_id": owner,
        "object_id": "object:o",
        "statement": f"cause {owner}",
        "observed_problem": "a problem",
        "primary_locus": None,
        "causal_depth": CausalDepth.UNDETERMINED,
        "scope_conditions": {"condition": "alpha"},
        "support_evidence_refs": ("span:0",),
        "counterevidence_refs": (),
        "counterevidence_queries": ("look for a counterexample",),
        "assumptions": (),
        "uncertainty": "unvalidated",
        "predicted_observations": (f"observation of {owner}",),
        "discriminating_tests": (
            {
                "test_id": f"t-{owner}",
                "procedure_candidate": "compare",
                "expected_if_true": "worse",
                "expected_if_alternative": "same",
                "risk_tier": RiskTier.R0,
                "reversibility": Reversibility.FULL,
                "expected_by_hypothesis": tuple(
                    {"hypothesis_id": target, "expected": label, "basis": ()}
                    for target, label in table.items()
                ),
            },
        ),
        "status": HypothesisStatus.DRAFT,
    }


def portfolio(tables: dict[str, dict[str, str]]) -> HypothesisPortfolioV3:
    return HypothesisPortfolioV3.model_validate(
        {
            "portfolio_id": "portfolio:o",
            "object_id": "object:o",
            "hypotheses": tuple(hypothesis(owner, tables.get(owner, {})) for owner in IDS),
            "status": PortfolioStatus.DRAFT,
            "generated_from_head_set": HEAD,
            "alternatives_considered": ("another",),
            "next_checks": ("check",),
            "uncertainty_reserve": "unknown",
        }
    )


def table_of(result: HypothesisPortfolio, owner: str) -> dict[str, str]:
    (hyp,) = [item for item in result.hypotheses if item.hypothesis_id == owner]
    (test,) = hyp.discriminating_tests
    return {row.hypothesis_id: row.expected for row in test.expected_by_hypothesis}  # type: ignore[attr-defined]


def row(
    owner: str, target: str, expected: str, *basis: str, reason: str | None = None
) -> FilledRow:
    return FilledRow(hypothesis_id=target, expected=expected, basis=basis, unknown_reason=reason)


def fill_of(owner: str, *rows: FilledRow) -> FilledTest:
    return FilledTest(designed_for=owner, test_id=f"t-{owner}", rows=rows)


def dropped(result: HypothesisPortfolio, owner: str) -> list[tuple[str, str, str]]:
    (hyp,) = [item for item in result.hypotheses if item.hypothesis_id == owner]
    return [(d.test_id, d.hypothesis_id, d.reason) for d in hyp.dropped]  # type: ignore[attr-defined]


SPANS = {span.span_id for span in source(3)}


def merged(
    start: HypothesisPortfolioV3, *tests: FilledTest, problem: str = KOREAN
) -> tuple[HypothesisPortfolioV3, int]:
    result, changed, _ = merge_fill(start, HypothesisTableFill(tests=tests), SPANS, problem)
    return result, changed


# --- the merge rule -----------------------------------------------------------------------------


def test_only_missing_and_unknown_cells_change_and_the_designed_for_cell_stays() -> None:
    start = portfolio({"h1": {"h1": WORSE, "h2": "모름"}, "h2": {"h2": WORSE, "h3": SAME}})
    result, changed = merged(
        start,
        fill_of("h1", row("h1", "h2", SAME), row("h1", "h3", SAME)),  # h2 was 모름, h3 missing
        fill_of("h2", row("h2", "h3", "다른 표현"), row("h2", "h2", "다른 값")),
    )
    assert table_of(result, "h1") == {"h1": WORSE, "h2": SAME, "h3": SAME}
    # a cell the generator already filled is not overwritten, and neither is a designed-for cell
    assert table_of(result, "h2") == {"h2": WORSE, "h3": SAME}
    assert changed == 2
    assert {reason for _, _, reason in dropped(result, "h2")} == {"TABLE_FILL_CELL_NOT_OPEN"}


def test_a_designed_for_cell_that_is_missing_or_unknown_is_left_as_the_generator_wrote_it() -> None:
    start = portfolio({"h1": {"h1": "모름", "h2": "모름"}})
    result, changed = merged(start, fill_of("h1", row("h1", "h1", WORSE), row("h1", "h2", SAME)))
    assert table_of(result, "h1") == {"h1": "모름", "h2": SAME} and changed == 1
    assert dropped(result, "h1") == [("t-h1", "h1", "TABLE_FILL_CELL_NOT_OPEN")]


def test_an_unknown_answer_for_a_missing_cell_makes_the_cell_explicit_and_is_not_a_change() -> None:
    start = portfolio({"h1": {"h1": WORSE}})
    result, changed = merged(start, fill_of("h1", row("h1", "h2", "모름"), row("h1", "h3", SAME)))
    assert table_of(result, "h1") == {"h1": WORSE, "h2": "모름", "h3": SAME}
    assert changed == 1


def test_an_unknown_answer_leaves_an_unknown_cell_unknown() -> None:
    start = portfolio({"h1": {"h1": WORSE, "h2": "모름"}})
    result, changed = merged(start, fill_of("h1", row("h1", "h2", "모름")))
    assert table_of(result, "h1") == {"h1": WORSE, "h2": "모름"} and changed == 0


def test_existing_rows_keep_their_place_and_new_ones_follow_in_portfolio_order() -> None:
    start = portfolio({"h1": {"h3": SAME, "h1": WORSE}})
    result, _ = merged(start, fill_of("h1", row("h1", "h2", SAME)))
    assert list(table_of(result, "h1")) == ["h3", "h1", "h2"]


def test_an_answer_that_cannot_be_kept_is_dropped_alone_and_recorded_on_its_test() -> None:
    start = portfolio({"h1": {"h1": WORSE}})
    result, changed = merged(
        start,
        fill_of(
            "h1",
            row("h1", "h2", SAME),  # kept
            row("h1", "h3", SAME, "span:nowhere"),
            row("h1", "h3", "x" * 90),
            row("h1", "h3", "탐지가 下降한다"),
            row("h1", "h3", "gets worse"),
            row("h1", "nobody", SAME),
            row("h1", "h2", WORSE),  # a second answer for the same cell
        ),
        FilledTest(designed_for="h1", test_id="no-such-test", rows=(row("h1", "h2", SAME),)),
    )
    assert table_of(result, "h1") == {"h1": WORSE, "h2": SAME} and changed == 1
    reasons = [(h, reason) for _, h, reason in dropped(result, "h1")]
    assert reasons == [
        ("h3", "UNKNOWN_SPAN"),
        ("h3", "EXPECTED_TOO_LONG"),
        ("h3", "EXPECTED_SCRIPT_MISMATCH"),
        ("h3", "EXPECTED_LANGUAGE_MISMATCH"),
        ("nobody", "UNKNOWN_HYPOTHESIS"),
        ("h2", "DUPLICATE_HYPOTHESIS"),
        ("", "UNKNOWN_TEST"),
    ]
    texts = {d.reason: d.text for d in result.hypotheses[0].dropped}  # type: ignore[attr-defined]
    assert texts["EXPECTED_LANGUAGE_MISMATCH"] == "gets worse"


def test_an_english_question_takes_labels_in_any_language() -> None:
    start = portfolio({"h1": {"h1": WORSE}})
    result, changed = merged(
        start, fill_of("h1", row("h1", "h2", "gets worse")), problem="Why did detection fail?"
    )
    assert table_of(result, "h1")["h2"] == "gets worse" and changed == 1


def test_the_basis_keeps_known_spans_and_at_most_five() -> None:
    start = portfolio({"h1": {"h1": WORSE}})
    result, _ = merged(start, fill_of("h1", row("h1", "h2", SAME, "span:0", "span:1")))
    (hyp,) = result.hypotheses[:1]
    rows = hyp.discriminating_tests[0].expected_by_hypothesis  # type: ignore[attr-defined]
    assert [r.basis for r in rows if r.hypothesis_id == "h2"] == [("span:0", "span:1")]


def test_the_cells_are_counted_over_every_test_and_hypothesis() -> None:
    start = portfolio({"h1": {"h1": WORSE, "h2": "모름"}, "h2": {"h2": WORSE}})
    assert count_cells(start) == (9, 7)  # h1: h2 and h3 open; h2: h1 and h3; h3: all three
    result, _ = merged(start, fill_of("h1", row("h1", "h2", SAME), row("h1", "h3", SAME)))
    assert count_cells(result) == (9, 5)


# --- the call -----------------------------------------------------------------------------------


@dataclass
class Command:
    evidence: tuple[EvidenceSpan, ...] = field(default_factory=lambda: tuple(source(3)))
    cutoff_at: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    model_policy_ref: str = "policy:1"


def context(command: Command) -> ContextPack:
    return ContextPack(
        case_id="case",
        project_id="p",
        object_id="object:o",
        problem=KOREAN,
        evidence=command.evidence,
        criteria=(),
        sufficiency=None,
        input_head_set_digest=HEAD,
        research_context={"big": "the whole research context of the generator call"},
    )


class FillerModel:
    def __init__(self, answer: HypothesisTableFill | BaseException) -> None:
        self.answer = answer
        self.calls: list[ModelRequest[Any]] = []

    async def structured(self, request: ModelRequest[Any]) -> ModelResult[Any]:
        self.calls.append(request)
        if isinstance(self.answer, BaseException):
            raise self.answer
        parsed: BaseModel = request.output_model.model_validate(self.answer.model_dump())
        return ModelResult(
            output=parsed,
            model_id="CONTROLLED",
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest=model_digest("INPUT", request.context_pack, schema_version="1.0.0"),
            output_digest=model_digest("OUTPUT", parsed, schema_version="1.0.0"),
        )


async def filled(model: FillerModel, start: HypothesisPortfolio) -> HypothesisPortfolio:
    command = Command()
    return await fill_tables(model, start, context(command), command)  # type: ignore[arg-type]


OPEN = {"h1": {"h1": WORSE, "h2": "모름"}}


@pytest.mark.asyncio
async def test_the_call_asks_for_the_open_cells_only_and_the_record_says_what_changed() -> None:
    model = FillerModel(HypothesisTableFill(tests=(fill_of("h1", row("h1", "h2", SAME)),)))
    result = await filled(model, portfolio(OPEN))
    (call,) = model.calls
    assert call.role == ModelRole.HYPOTHESIS_TABLE_FILLER
    assert call.prompt_version == TABLE_FILLER_VERSION and call.output_model is HypothesisTableFill
    assert call.max_output_tokens == TABLE_FILLER_MAX_OUTPUT_TOKENS < 4_000
    sent = call.context_pack.research_context
    assert "big" not in sent  # the generator's research context is not sent again
    tests = sent["table_filler"]["tests"]  # type: ignore[index]
    assert {(t["designed_for"], tuple(t["open_hypothesis_ids"])) for t in tests} == {
        ("h1", ("h2", "h3")),
        ("h2", ("h1", "h3")),
        ("h3", ("h1", "h2")),
    }
    assert call.context_pack.problem == KOREAN and call.context_pack.evidence
    record = result.table_fill  # type: ignore[attr-defined]
    assert (record.state, record.reason_code, record.changed_cells) == ("CALLED", None, 1)
    # every cell it was asked about has one entry: one value, the rest given no row
    assert [a.outcome for a in record.answers].count("CHANGED") == 1
    assert [a.outcome for a in record.answers].count("NOT_ANSWERED") == 5
    assert len(record.answers) == 6  # the designed-for cells of the other tests are not asked
    assert (record.cells, record.open_before, record.open_after) == (9, 8, 7)
    assert table_of(result, "h1") == {"h1": WORSE, "h2": SAME}


@pytest.mark.asyncio
async def test_a_full_table_makes_no_call_and_says_so() -> None:
    full = {owner: {target: WORSE if owner == target else SAME for target in IDS} for owner in IDS}
    model = FillerModel(HypothesisTableFill())
    result = await filled(model, portfolio(full))
    assert model.calls == []
    record = result.table_fill  # type: ignore[attr-defined]
    assert (record.state, record.cells, record.open_before, record.open_after) == (
        "SKIPPED_FULL",
        9,
        0,
        0,
    )


@pytest.mark.asyncio
async def test_one_hypothesis_or_a_v2_portfolio_makes_no_call_and_changes_nothing() -> None:
    model = FillerModel(HypothesisTableFill())
    one = portfolio(OPEN).model_copy(update={"hypotheses": portfolio(OPEN).hypotheses[:1]})
    assert await filled(model, one) is one
    v2 = HypothesisPortfolio.model_validate(
        {**portfolio(OPEN).model_dump(), "hypotheses": ()},
    )
    assert await filled(model, v2) is v2
    assert model.calls == []
    assert getattr(one, "table_fill", None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "reason"),
    [
        (ModelExecutionHold("OAUTH_TRANSPORT_FAILURE"), "FAILED", "OAUTH_TRANSPORT_FAILURE"),
        (ModelExecutionHold("SERIALIZED_MODEL_INPUT_LIMIT"), "SKIPPED_BUDGET", None),
        (ModelExecutionHold("RESEARCH_BUDGET_MISSING"), "SKIPPED_BUDGET", None),
    ],
)
async def test_a_call_that_fails_or_hits_a_limit_leaves_the_table_and_records_why(
    error: BaseException, state: str, reason: str | None
) -> None:
    start = portfolio(OPEN)
    result = await filled(FillerModel(error), start)
    record = result.table_fill  # type: ignore[attr-defined]
    assert record.state == state and record.reason_code == (reason or str(error))
    assert (record.changed_cells, record.open_before, record.open_after) == (0, 8, 8)
    assert record.answers == ()
    assert [table_of(result, owner) for owner in IDS] == [table_of(start, owner) for owner in IDS]


@pytest.mark.asyncio
async def test_an_answer_that_does_not_fit_the_form_is_a_failure_not_a_crash() -> None:
    class BadForm(FillerModel):
        async def structured(self, request: ModelRequest[Any]) -> ModelResult[Any]:
            request.output_model.model_validate({"tests": [{"unexpected": 1}]})
            raise AssertionError("not reached")

    result = await filled(BadForm(HypothesisTableFill()), portfolio(OPEN))
    record = result.table_fill  # type: ignore[attr-defined]
    assert (record.state, record.reason_code) == ("FAILED", "TABLE_FILL_OUTPUT_INVALID")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [CancelledError(), ResearchFence("CANCELLED_OR_THREAD_STOPPED")])
async def test_a_stopped_research_is_not_swallowed(error: BaseException) -> None:
    with pytest.raises(type(error)):
        await filled(FillerModel(error), portfolio(OPEN))


@pytest.mark.asyncio
async def test_bad_generator_rows_are_settled_before_the_call_asks_for_the_cells() -> None:
    generated = portfolio({"h1": {"h1": WORSE, "h2": "모름"}})
    bad = (
        generated.hypotheses[0]
        .discriminating_tests[0]
        .model_copy(
            update={
                "expected_by_hypothesis": (
                    *generated.hypotheses[0].discriminating_tests[0].expected_by_hypothesis,
                    ExpectedResult(hypothesis_id="h3", expected="x", basis=("span:made-up",)),
                )
            }
        )
    )
    first = generated.hypotheses[0].model_copy(update={"discriminating_tests": (bad,)})
    generated = generated.model_copy(update={"hypotheses": (first, *generated.hypotheses[1:])})
    model = FillerModel(HypothesisTableFill(tests=(fill_of("h1", row("h1", "h3", SAME)),)))
    command = Command()
    result = await accepted_portfolio(model, generated, context(command), command, False)  # type: ignore[arg-type]
    (call,) = model.calls
    sent = call.context_pack.research_context["table_filler"]["tests"]  # type: ignore[index]
    one = next(t for t in sent if t["designed_for"] == "h1")
    assert one["open_hypothesis_ids"] == ["h2", "h3"]  # the made-up row was settled away first
    assert table_of(result, "h1")["h3"] == SAME
    assert ("t-h1", "h3", "UNKNOWN_SPAN") in dropped(result, "h1")


# --- what the filler said for each cell it was asked about ---------------------------------------


def answers_of(
    start: HypothesisPortfolioV3, *tests: FilledTest
) -> dict[tuple[str, str], tuple[str, str | None, str]]:
    """(test id, hypothesis id) -> (expected, unknown_reason, outcome) for every asked cell."""
    _, _, found = merge_fill(start, HypothesisTableFill(tests=tests), SPANS, KOREAN)
    return {(a.test_id, a.hypothesis_id): (a.expected, a.unknown_reason, a.outcome) for a in found}


def test_each_asked_cell_gets_one_outcome_and_unasked_cells_get_none() -> None:
    start = portfolio({"h1": {"h1": WORSE, "h2": "모름"}, "h2": {"h2": WORSE, "h3": SAME}})
    reason = "설정을 바꾸면 어느 쪽 결과도 나올 수 있다"
    found = answers_of(
        start,
        fill_of(
            "h1",
            row("h1", "h2", "모름", reason=reason),  # asked, still unknown, with its reason
            row("h1", "h3", SAME),  # asked (missing), now a value
            row("h1", "h1", WORSE),  # the designed-for cell was not asked
        ),
        fill_of("h2", row("h2", "h1", SAME, "span:nowhere")),  # asked, answer cannot be kept
    )
    assert found[("t-h1", "h2")] == ("모름", reason, "STILL_UNKNOWN")
    assert found[("t-h1", "h3")] == (SAME, None, "CHANGED")
    assert found[("t-h2", "h1")] == (SAME, None, "DROPPED")
    # asked but given no row: NOT_ANSWERED (the h3 test has two open cells, none answered)
    assert {key for key in found if key[0] == "t-h3"} == {("t-h3", "h1"), ("t-h3", "h2")}
    assert {value[2] for key, value in found.items() if key[0] == "t-h3"} == {"NOT_ANSWERED"}
    assert ("t-h1", "h1") not in found  # not asked, so absent (it is only recorded as dropped)


def test_a_reason_is_kept_only_for_an_unknown_answer_and_is_cut_to_200_characters() -> None:
    start = portfolio({"h1": {"h1": WORSE}})
    long = "이유 " * 300
    found = answers_of(
        start,
        fill_of(
            "h1",
            row("h1", "h2", "모름", reason=long),
            row("h1", "h3", SAME, reason="값을 쓸 때는 이유를 쓰지 않는다"),
        ),
    )
    assert found[("t-h1", "h2")][1] == long.strip()[:200]
    assert found[("t-h1", "h3")][1] is None


def test_a_kept_second_answer_replaces_a_dropped_first_one_and_a_repeat_changes_nothing() -> None:
    start = portfolio({"h1": {"h1": WORSE}})
    found = answers_of(
        start,
        fill_of(
            "h1",
            row("h1", "h2", "x" * 90),  # too long: dropped
            row("h1", "h2", SAME),  # kept: replaces the dropped one
            row("h1", "h3", SAME),
            row("h1", "h3", WORSE),  # a repeat of a kept answer: the first stays
        ),
    )
    assert found[("t-h1", "h2")][2] == "CHANGED"
    assert found[("t-h1", "h3")] == (SAME, None, "CHANGED")


def test_a_cell_that_does_not_fit_in_the_table_is_reported_as_dropped() -> None:
    names = [f"x{n}" for n in range(10)]
    full = hypothesis("h1", {name: SAME for name in names})
    other = hypothesis("h2", {})
    base = HypothesisPortfolioV3.model_validate(
        {**portfolio({}).model_dump(), "hypotheses": (full, other)}
    )
    # ten rows already name hypotheses that are not in this portfolio; the eleventh does not fit
    found = answers_of(base, fill_of("h1", row("h1", "h2", SAME)))
    assert found[("t-h1", "h2")][2] == "DROPPED"


# --- a designed-for cell that settling dropped for its language ---------------------------------


def with_drop(start: HypothesisPortfolioV3, owner: str, target: str, reason: str) -> None:
    (item,) = [h for h in start.hypotheses if h.hypothesis_id == owner]
    item.note_dropped(
        (DroppedExpectation(test_id=f"t-{owner}", hypothesis_id=target, reason=reason, text="x"),)
    )


def test_a_designed_for_cell_dropped_for_its_language_may_be_filled_by_the_call() -> None:
    start = portfolio({"h1": {"h2": SAME, "h3": SAME}})
    with_drop(start, "h1", "h1", "EXPECTED_SCRIPT_MISMATCH")
    result, changed = merged(start, fill_of("h1", row("h1", "h1", WORSE)))
    assert table_of(result, "h1") == {"h2": SAME, "h3": SAME, "h1": WORSE} and changed == 1
    # the drop that opened the cell stays on the record
    assert ("t-h1", "h1", "EXPECTED_SCRIPT_MISMATCH") in dropped(result, "h1")


def test_a_designed_for_cell_stays_closed_unless_a_language_drop_removed_its_label() -> None:
    for reason, target in (
        ("UNKNOWN_SPAN", "h1"),  # dropped for another reason
        ("EXPECTED_SCRIPT_MISMATCH", "h2"),  # a language drop of another hypothesis's cell
        ("DUPLICATE_HYPOTHESIS", "h1"),
    ):
        start = portfolio({"h1": {"h2": SAME, "h3": SAME}})
        with_drop(start, "h1", target, reason)
        result, changed = merged(start, fill_of("h1", row("h1", "h1", WORSE)))
        assert "h1" not in table_of(result, "h1") and changed == 0
        assert ("t-h1", "h1", "TABLE_FILL_CELL_NOT_OPEN") in dropped(result, "h1")


def test_a_designed_for_cell_that_has_its_label_is_not_opened_by_an_old_language_drop() -> None:
    start = portfolio({"h1": {"h1": WORSE, "h2": SAME, "h3": SAME}})
    with_drop(start, "h1", "h1", "EXPECTED_LANGUAGE_MISMATCH")
    result, changed = merged(start, fill_of("h1", row("h1", "h1", SAME)))
    assert table_of(result, "h1")["h1"] == WORSE and changed == 0


@pytest.mark.asyncio
async def test_the_call_asks_for_a_designed_for_cell_that_a_language_drop_emptied() -> None:
    start = portfolio({"h1": {"h2": SAME}})
    with_drop(start, "h1", "h1", "EXPECTED_SCRIPT_MISMATCH")
    model = FillerModel(HypothesisTableFill(tests=(fill_of("h1", row("h1", "h1", WORSE)),)))
    result = await filled(model, start)
    (call,) = model.calls
    tests = call.context_pack.research_context["table_filler"]["tests"]  # type: ignore[index]
    asked = {t["designed_for"]: tuple(t["open_hypothesis_ids"]) for t in tests}
    assert asked["h1"] == ("h1", "h3") and asked["h2"] == ("h1", "h3")
    assert table_of(result, "h1")["h1"] == WORSE
