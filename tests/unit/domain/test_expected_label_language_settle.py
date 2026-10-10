"""A table label in the wrong language or script is dropped and recorded for a Korean question.

Settling has no model call. A label with no Hangul, or with Chinese characters or kana the question
itself does not have, is dropped alone with its original text; a question without Hangul leaves
every label as it was. A dropped cell is then open for the table-filling call.
"""

from __future__ import annotations

from typing import Any

from tests.unit.domain.test_refutation_language_settle import (
    ENGLISH_QUESTION,
    KOREAN_QUESTION,
    _hypothesis,
    _portfolio,
)

from thoth.application.services.hypothesis_contract_v3 import settle_contract
from thoth.domain.hypothesis import HypothesisPortfolioV3

GOOD = "탐지가 더 나빠진다"
KANA = "정답 대응また는 집계 오류로 과소 집계됨"
HANJA = "確認된 설정에서 누락이 늘어남"
NO_HANGUL = "stays slow"


def _with_table(hypothesis_id: str, rows: dict[str, str]) -> dict[str, Any]:
    item = _hypothesis(hypothesis_id, ())
    (test,) = item["discriminating_tests"]
    test["expected_by_hypothesis"] = tuple(
        {"hypothesis_id": target, "expected": label, "basis": ()} for target, label in rows.items()
    )
    return item


def _settled(problem: str, rows: dict[str, str]) -> HypothesisPortfolioV3:
    others = [_with_table(f"h{number}", {}) for number in range(2, 6)]
    portfolio = _portfolio(_with_table("h1", rows), *others)
    settled = settle_contract(portfolio, (), problem=problem)
    assert isinstance(settled, HypothesisPortfolioV3)
    return settled


def _table(settled: HypothesisPortfolioV3) -> dict[str, str]:
    (test,) = settled.hypotheses[0].discriminating_tests
    return {row.hypothesis_id: row.expected for row in test.expected_by_hypothesis}


def test_a_korean_question_drops_labels_with_kana_chinese_characters_or_no_hangul() -> None:
    settled = _settled(
        KOREAN_QUESTION,
        {"h1": GOOD, "h2": KANA, "h3": HANJA, "h4": NO_HANGUL, "h5": "모름"},
    )
    # the good label and 모름 stay; the three that are not Korean are gone, each recorded as it was
    assert _table(settled) == {"h1": GOOD, "h5": "모름"}
    assert [
        (d.test_id, d.hypothesis_id, d.reason, d.text) for d in settled.hypotheses[0].dropped
    ] == [
        ("t1", "h2", "EXPECTED_SCRIPT_MISMATCH", KANA),
        ("t1", "h3", "EXPECTED_SCRIPT_MISMATCH", HANJA),
        ("t1", "h4", "EXPECTED_LANGUAGE_MISMATCH", NO_HANGUL),
    ]


def test_the_designed_for_cell_is_dropped_the_same_way_and_the_drop_names_that_cell() -> None:
    settled = _settled(KOREAN_QUESTION, {"h1": KANA, "h2": GOOD})
    assert _table(settled) == {"h2": GOOD}
    (drop,) = settled.hypotheses[0].dropped
    assert (drop.hypothesis_id, drop.reason) == ("h1", "EXPECTED_SCRIPT_MISMATCH")


def test_a_question_without_hangul_or_with_the_script_itself_drops_no_label() -> None:
    rows = {"h1": GOOD, "h2": KANA, "h3": HANJA, "h4": NO_HANGUL}
    for problem in (ENGLISH_QUESTION, "", "検出基準が不合格です。"):
        settled = _settled(problem, rows)
        assert _table(settled) == rows
        assert settled.hypotheses[0].dropped == ()
    with_kana = _settled(KOREAN_QUESTION + " また", {"h1": KANA})
    assert _table(with_kana) == {"h1": KANA}


def test_a_label_dropped_for_its_language_is_not_also_dropped_for_something_else() -> None:
    # an unknown hypothesis is dropped for that reason first, with no text
    settled = _settled(KOREAN_QUESTION, {"h1": GOOD, "ghost": KANA})
    assert [(d.hypothesis_id, d.reason, d.text) for d in settled.hypotheses[0].dropped] == [
        ("ghost", "UNKNOWN_HYPOTHESIS", None)
    ]
