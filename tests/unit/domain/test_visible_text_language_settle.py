"""Foreign script in the readable text of a hypothesis: items are dropped, single texts flagged.

For a Korean question the v3 settling step drops a list item (missing evidence, counterevidence
queries, predicted observations, assumptions) with no Hangul or with Chinese characters or kana the
question and the supplied evidence do not have, and records it. A single text (statement,
uncertainty, test text) is never removed: it stays and a FLAGGED record is made. Quoted characters
are not a problem, an English question changes nothing, and a v2 result is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from tests.unit.domain.test_refutation_language_settle import (
    ENGLISH_QUESTION,
    KOREAN_QUESTION,
    _hypothesis,
    _portfolio,
)

from thoth.application.services.hypothesis_contract_v3 import settle_contract
from thoth.application.services.text_script import KANA as KANA_LETTERS
from thoth.application.services.text_script import language_problem
from thoth.domain.hypothesis import HypothesisPortfolio, HypothesisPortfolioV3

GOOD = "시행별 정답과 탐지 기록"
KANA = "독립した 반복 관측 결과"
HANJA = "確認된 설정 이력"
ENGLISH = "per-trial ground truth"


@dataclass
class Span:  # only what settling reads
    span_id: str
    exact_text: str


def _with(**changes: Any) -> dict[str, Any]:
    item = _hypothesis("h1", ())
    item.update(changes)
    return item


def _settled(item: dict[str, Any], problem: str, *spans: Span) -> HypothesisPortfolioV3:
    portfolio = _portfolio(item, _hypothesis("h2", ()))
    settled = settle_contract(portfolio, spans, problem=problem)  # type: ignore[arg-type]
    assert isinstance(settled, HypothesisPortfolioV3)
    return settled


def _records(settled: HypothesisPortfolioV3) -> list[tuple[str, str, str, str | None]]:
    return [(d.test_id, d.hypothesis_id, d.reason, d.text) for d in settled.hypotheses[0].dropped]


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("missing_evidence", "MISSING_EVIDENCE"),
        ("counterevidence_queries", "COUNTEREVIDENCE_QUERIES"),
        ("predicted_observations", "PREDICTED_OBSERVATIONS"),
        ("assumptions", "ASSUMPTIONS"),
    ],
)
def test_a_list_item_with_kana_chinese_characters_or_no_hangul_is_dropped_and_recorded(
    field: str, reason: str
) -> None:
    settled = _settled(_with(**{field: (GOOD, KANA, HANJA, ENGLISH)}), KOREAN_QUESTION)
    assert getattr(settled.hypotheses[0], field) == (GOOD,)
    assert _records(settled) == [
        ("", "h1", f"{reason}_SCRIPT_MISMATCH", KANA),
        ("", "h1", f"{reason}_SCRIPT_MISMATCH", HANJA),
        ("", "h1", f"{reason}_LANGUAGE_MISMATCH", ENGLISH),
    ]


def test_a_single_text_stays_and_is_flagged_with_its_test_when_it_has_one() -> None:
    item = _with(statement=HANJA, uncertainty="まだ 확인되지 않음")
    (test,) = item["discriminating_tests"]
    test.update(
        procedure_candidate="独立 재집계를 한다",
        expected_if_true=GOOD,
        expected_if_alternative=KANA,
    )
    settled = _settled(item, KOREAN_QUESTION)
    kept = settled.hypotheses[0]
    assert (kept.statement, kept.uncertainty) == (HANJA, "まだ 확인되지 않음")
    assert kept.discriminating_tests[0].procedure_candidate == "独立 재집계를 한다"
    assert kept.discriminating_tests[0].expected_if_alternative == KANA
    assert _records(settled) == [
        ("", "h1", "STATEMENT_SCRIPT_FLAGGED", HANJA),
        ("", "h1", "UNCERTAINTY_SCRIPT_FLAGGED", "まだ 확인되지 않음"),
        ("t1", "h1", "PROCEDURE_CANDIDATE_SCRIPT_FLAGGED", "独立 재집계를 한다"),
        ("t1", "h1", "EXPECTED_IF_ALTERNATIVE_SCRIPT_FLAGGED", KANA),
    ]


def test_a_single_text_with_no_hangul_is_flagged_for_its_language_and_kept() -> None:
    settled = _settled(_with(statement="a cause"), KOREAN_QUESTION)
    assert settled.hypotheses[0].statement == "a cause"
    assert _records(settled) == [("", "h1", "STATEMENT_LANGUAGE_FLAGGED", "a cause")]


def test_characters_quoted_from_the_question_or_the_evidence_are_not_a_problem() -> None:
    quoted = "기록의 檢證 열을 본다"
    quoted_kana = "원문 'まだ' 표기를 본다"
    item = _with(missing_evidence=(quoted, quoted_kana), statement=quoted)
    unquoted = _settled(item, KOREAN_QUESTION)
    assert unquoted.hypotheses[0].missing_evidence == ()
    evidence = (Span("span:a", "자료 헤더: 檢證, まだ"),)
    settled = _settled(item, KOREAN_QUESTION, *evidence)
    assert settled.hypotheses[0].missing_evidence == (quoted, quoted_kana)
    assert _records(settled) == []
    in_question = _settled(item, KOREAN_QUESTION + " 檢證 まだ")
    assert in_question.hypotheses[0].missing_evidence == (quoted, quoted_kana)
    assert _records(in_question) == []


def test_the_only_missing_evidence_a_hypothesis_has_is_flagged_not_dropped() -> None:
    # a hypothesis with no support refs needs one gap; removing it would make the record invalid
    item = _with(support_evidence_refs=(), missing_evidence=(KANA,))
    settled = _settled(item, KOREAN_QUESTION)
    assert settled.hypotheses[0].missing_evidence == (KANA,)
    assert _records(settled) == [("", "h1", "MISSING_EVIDENCE_SCRIPT_FLAGGED", KANA)]
    with_support = _settled(_with(missing_evidence=(KANA,)), KOREAN_QUESTION)
    assert with_support.hypotheses[0].missing_evidence == ()


def test_the_only_counterevidence_query_is_flagged_when_nothing_else_stands_for_it() -> None:
    # the portfolio check rejects a hypothesis with neither a counterevidence ref nor a query
    alone = _settled(_with(counterevidence_queries=(KANA,)), KOREAN_QUESTION)
    assert alone.hypotheses[0].counterevidence_queries == (KANA,)
    assert _records(alone) == [("", "h1", "COUNTEREVIDENCE_QUERIES_SCRIPT_FLAGGED", KANA)]
    backed = _with(counterevidence_queries=(KANA,), counterevidence_refs=("span:a",))
    dropped = _settled(backed, KOREAN_QUESTION)
    assert dropped.hypotheses[0].counterevidence_queries == ()
    assert _records(dropped) == [("", "h1", "COUNTEREVIDENCE_QUERIES_SCRIPT_MISMATCH", KANA)]


def test_an_english_question_changes_nothing() -> None:
    item = _with(
        missing_evidence=(KANA, ENGLISH), statement=HANJA, assumptions=(HANJA,), uncertainty=KANA
    )
    for problem in (ENGLISH_QUESTION, ""):
        settled = _settled(item, problem)
        assert settled.hypotheses[0].missing_evidence == (KANA, ENGLISH)
        assert settled.hypotheses[0].dropped == ()


def test_a_v2_result_is_never_touched() -> None:
    dumped = _portfolio(_with(missing_evidence=(KANA,)), _hypothesis("h2", ())).model_dump()
    for item in dumped["hypotheses"]:
        item.pop("refutation_conditions")
        for test in item["discriminating_tests"]:
            test.pop("expected_by_hypothesis")
    plain = HypothesisPortfolio.model_validate(dumped)
    assert not isinstance(plain, HypothesisPortfolioV3)
    assert settle_contract(plain, (), problem=KOREAN_QUESTION) is plain


DOT = "\u30fb"  # the katakana middle dot: punctuation, not a kana letter
KOREAN_DOT = "\u00b7"


def test_the_katakana_middle_dot_is_punctuation_and_a_kana_letter_is_not() -> None:
    assert KANA_LETTERS.search(DOT) is None and KANA_LETTERS.search("ー") is not None
    assert KANA_LETTERS.search("した") is not None and KANA_LETTERS.search("また") is not None
    assert language_problem(f"조건{DOT}측정계", KOREAN_QUESTION) is None
    assert language_problem("독립した 반복", KOREAN_QUESTION) == "SCRIPT"
    assert language_problem(f"독립した{DOT}반복", KOREAN_QUESTION) == "SCRIPT"


def test_a_middle_dot_in_any_readable_text_becomes_the_korean_one_and_nothing_is_dropped() -> None:
    dotted = f"조건{DOT}측정계{DOT}계산식을 검증한다"
    fixed = f"조건{KOREAN_DOT}측정계{KOREAN_DOT}계산식을 검증한다"
    item = _with(
        statement=dotted,
        uncertainty=dotted,
        missing_evidence=(dotted, GOOD),
        counterevidence_queries=(dotted,),
        predicted_observations=(dotted,),
        assumptions=(dotted,),
        refutation_conditions=(dotted,),
    )
    (test,) = item["discriminating_tests"]
    test.update(procedure_candidate=dotted, expected_if_true=dotted, expected_if_alternative=dotted)
    test["expected_by_hypothesis"] = ({"hypothesis_id": "h1", "expected": dotted, "basis": ()},)
    settled = _settled(item, KOREAN_QUESTION)
    kept = settled.hypotheses[0]
    assert (kept.statement, kept.uncertainty) == (fixed, fixed)
    assert kept.missing_evidence == (fixed, GOOD)
    assert kept.counterevidence_queries == kept.predicted_observations == (fixed,)
    assert kept.assumptions == kept.refutation_conditions == (fixed,)
    shown = kept.discriminating_tests[0]
    assert (shown.procedure_candidate, shown.expected_if_true) == (fixed, fixed)
    assert shown.expected_if_alternative == fixed
    assert [row.expected for row in shown.expected_by_hypothesis] == [fixed]
    assert _records(settled) == []  # nothing was dropped or flagged, and the change is not recorded


def test_a_real_kana_next_to_a_middle_dot_is_still_caught() -> None:
    settled = _settled(_with(missing_evidence=(f"독립した{DOT}반복", GOOD)), KOREAN_QUESTION)
    assert settled.hypotheses[0].missing_evidence == (GOOD,)
    assert [reason for _, _, reason, _ in _records(settled)] == ["MISSING_EVIDENCE_SCRIPT_MISMATCH"]


def test_a_middle_dot_the_question_or_the_evidence_has_is_left_as_it_is() -> None:
    item = _with(missing_evidence=(f"원문의 {DOT} 표기를 본다",))
    quoted = _settled(item, KOREAN_QUESTION, Span("span:a", f"헤더: 가{DOT}나"))
    assert quoted.hypotheses[0].missing_evidence == (f"원문의 {DOT} 표기를 본다",)
    asked = _settled(item, KOREAN_QUESTION + f" {DOT}")
    assert asked.hypotheses[0].missing_evidence == (f"원문의 {DOT} 표기를 본다",)


def test_an_english_question_keeps_the_middle_dot() -> None:
    item = _with(statement=f"a{DOT}b", missing_evidence=(f"c{DOT}d",))
    for problem in (ENGLISH_QUESTION, ""):
        settled = _settled(item, problem)
        assert settled.hypotheses[0].statement == f"a{DOT}b"
        assert settled.hypotheses[0].missing_evidence == (f"c{DOT}d",)
