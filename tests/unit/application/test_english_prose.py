from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from tests.unit.application.test_hypothesis_table_filler import IDS, portfolio
from tests.unit.test_research_short_text_selection import source

from thoth.application.services.english_prose import EnglishProseCheck, record_english_check
from thoth.application.services.hypothesis_contract_v3 import settle_contract
from thoth.application.services.hypothesis_table_filler import accepted_portfolio, merge_fill
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import research_work


def context():
    return ContextPack(
        case_id="case:한글",
        project_id="p",
        object_id="object:o",
        problem="질문 원문: 「원래 질문」",
        evidence=source(3),
        criteria=(),
        sufficiency=None,
        input_head_set_digest="a" * 64,
        research_context={"source_overview": {"items": [{"title": "한국어 보고서"}]}},
    )


def filled_portfolio():
    data = portfolio(
        {owner: {target: "same result" for target in IDS} for owner in IDS}
    ).model_dump()
    for hypothesis in data["hypotheses"]:
        for test in hypothesis["discriminating_tests"]:
            for row in test["expected_by_hypothesis"]:
                row["knowledge_state"] = "PREDICTED"
    return HypothesisPortfolioV4.model_validate(data)


def test_verified_quotes_identifiers_titles_and_original_question_remain_protected():
    ctx = context()
    check = EnglishProseCheck.build(ctx.problem, ctx.evidence, context=ctx)
    assert check.quotes.has("원래 질문")
    for text in ("The user asked “원래 질문”.", "See 한국어 보고서 and case:한글.", ctx.problem):
        assert not check.mismatch(text)
    assert check.mismatch("새로 작성한 한글 설명")
    assert check.mismatch("The model invents “제공되지 않은 인용”.")
    assert check.mismatch("Unexpected ㄱㄴ")


def test_english_request_flags_new_hangul_without_dropping_valid_english_or_rewriting_data():
    ctx = context()
    generated = filled_portfolio()
    h = generated.hypotheses[0]
    test = h.discriminating_tests[0]
    bad = test.expected_by_hypothesis[0].model_copy(update={"expected": "새로 작성한 한글 설명"})
    changed = h.model_copy(
        update={
            "statement": "새로 작성한 가설 설명",
            "observed_problem": ctx.problem,
            "refutation_conditions": ("The result stays unchanged.",),
            "discriminating_tests": (
                test.model_copy(
                    update={"expected_by_hypothesis": (bad, *test.expected_by_hypothesis[1:])}
                ),
            ),
        }
    )
    generated = generated.model_copy(update={"hypotheses": (changed, *generated.hypotheses[1:])})
    work = SimpleNamespace(response_language="en", result_only={})
    token = research_work.set(work)
    try:
        settled = settle_contract(generated, ctx.evidence, ctx.problem, context=ctx)
        item = settled.hypotheses[0]
        assert item.statement == changed.statement
        assert item.refutation_conditions == changed.refutation_conditions
        assert item.discriminating_tests[0].expected_by_hypothesis[0].expected == bad.expected
        assert {flag.reason for flag in item.dropped} == {
            "STATEMENT_LANGUAGE_FLAGGED",
            "EXPECTED_LANGUAGE_FLAGGED",
        }
        record_english_check(settled, ctx)
        assert work.result_only["hypothesis_language_check"]["flagged_items"] == 2
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_script_flags_never_trigger_a_language_repair_call():
    class NoCalls:
        async def structured(self, request):
            raise AssertionError("language flags must not dispatch a repair call")

    ctx = context()
    generated = filled_portfolio()
    h = generated.hypotheses[0].model_copy(update={"statement": "새로 작성한 가설 설명"})
    generated = generated.model_copy(update={"hypotheses": (h, *generated.hypotheses[1:])})
    work = SimpleNamespace(response_language="en", result_only={})
    token = research_work.set(work)
    try:
        await accepted_portfolio(
            NoCalls(),
            generated,
            ctx,
            SimpleNamespace(
                evidence=ctx.evidence,
                cutoff_at=datetime(2026, 10, 12, tzinfo=UTC),
                model_policy_ref="policy:p",
            ),
            False,
        )
        assert work.result_only["hypothesis_language_check"]["flagged_items"] == 1
    finally:
        research_work.reset(token)


def test_filler_unknown_reason_is_checked_but_unknown_state_and_original_reason_are_preserved():
    ctx = context()
    generated = filled_portfolio()
    owner = generated.hypotheses[0]
    test = owner.discriminating_tests[0]
    rows = list(test.expected_by_hypothesis)
    rows[1] = rows[1].model_copy(update={"knowledge_state": "UNKNOWN", "expected": None})
    owner = owner.model_copy(
        update={
            "discriminating_tests": (
                test.model_copy(update={"expected_by_hypothesis": tuple(rows)}),
            )
        }
    )
    generated = generated.model_copy(update={"hypotheses": (owner, *generated.hypotheses[1:])})
    fill = HypothesisTableFillV4.model_validate(
        {
            "tests": [
                {
                    "designed_for": "h1",
                    "test_id": "t-h1",
                    "rows": [
                        {
                            "hypothesis_id": "h2",
                            "knowledge_state": "UNKNOWN",
                            "expected": None,
                            "unknown_reason": "새 한국어 이유",
                        },
                    ],
                }
            ]
        }
    )
    token = research_work.set(SimpleNamespace(response_language="en", result_only={}))
    try:
        merged, changed, answers = merge_fill(
            generated, fill, {s.span_id for s in ctx.evidence}, ctx.problem, context=ctx
        )
        assert changed == 0
        assert (
            answers[0].outcome == "STILL_UNKNOWN" and answers[0].unknown_reason == "새 한국어 이유"
        )
        assert merged.hypotheses[0].dropped[0].reason == "TABLE_FILL_REASON_LANGUAGE_FLAGGED"
    finally:
        research_work.reset(token)


def test_hypothesis_v2_prose_is_also_checked_when_the_table_option_is_off():
    ctx = context()
    data = filled_portfolio().model_dump()
    for hypothesis in data["hypotheses"]:
        hypothesis.pop("refutation_conditions")
        for test in hypothesis["discriminating_tests"]:
            test.pop("expected_by_hypothesis")
    data["hypotheses"][0]["statement"] = "새로 작성한 한글 가설"
    work = SimpleNamespace(response_language="en", result_only={})
    token = research_work.set(work)
    try:
        record_english_check(HypothesisPortfolio.model_validate(data), ctx)
        assert work.result_only["hypothesis_language_check"]["flagged_items"] == 1
    finally:
        research_work.reset(token)
