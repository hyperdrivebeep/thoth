"""A refutation condition with no Hangul is dropped, and recorded, when the question is Korean.

The v3 settling step has no model call: it only keeps what is in the question's language. A
question without Hangul (for example English) leaves every condition as it was.
"""

from __future__ import annotations

from typing import Any

from thoth.application.services.hypothesis_contract_v3 import (
    DroppedExpectation,
    settle_contract,
)
from thoth.domain.enums import (
    CausalDepth,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
    RiskTier,
)
from thoth.domain.hypothesis import HypothesisPortfolioV3

HEAD = "a" * 64
MISMATCH = "REFUTATION_LANGUAGE_MISMATCH"
KOREAN_QUESTION = '「SYN-C-DET-RAIN」 기준의 판정이 "미달"입니다. 원인 후보를 찾아 주세요.'
THIRD = (
    "관련 설정을 충분히 비교했는데도 실패가 설정과 무관하게 유지되면, "
    "검토한 설정 범위에서 이 후보를 제외한다."
)
FOURTH = (
    "관련 대상·조건의 구성이 같고 충분한 비교에서도 구성에 따른 차이가 없으면, "
    "검토한 구성 범위에서 이 후보를 제외한다."
)
FIFTH = (
    "자료가 무작위 생성이나 표본 추출 없이 고정 수치를 기입한 예시로 확인되면, "
    "우연 변동 후보를 제외한다.",
    "사전에 정한 충분한 독립 반복에서 지속적 저하가 확인되고 "
    "우연 변동만으로 설명할 수 없으면, 단일 실행 변동만의 설명을 제외한다.",
)
ENGLISH_QUESTION = "The detection criterion failed. Find the cause candidates and tests."


def _hypothesis(hypothesis_id: str, conditions: tuple[str, ...]) -> dict[str, Any]:
    return {
        "hypothesis_id": hypothesis_id,
        "object_id": "object:o",
        "statement": "처리 설정이 원인일 수 있다",
        "observed_problem": "a problem",
        "primary_locus": None,
        "causal_depth": CausalDepth.UNDETERMINED,
        "scope_conditions": {"condition": "alpha"},
        "support_evidence_refs": ("span:a",),
        "counterevidence_refs": (),
        "counterevidence_queries": ("반례 기록을 찾는다",),
        "assumptions": (),
        "uncertainty": "아직 확인되지 않았다",
        "predicted_observations": (),
        "discriminating_tests": (
            {
                "test_id": "t1",
                "procedure_candidate": "재처리해 비교한다",
                "expected_if_true": "누락",
                "expected_if_alternative": "유지",
                "risk_tier": RiskTier.R0,
                "reversibility": Reversibility.FULL,
            },
        ),
        "status": HypothesisStatus.DRAFT,
        "refutation_conditions": conditions,
    }


def _portfolio(*hypotheses: dict[str, Any]) -> HypothesisPortfolioV3:
    return HypothesisPortfolioV3.model_validate(
        {
            "portfolio_id": "portfolio:o",
            "object_id": "object:o",
            "hypotheses": hypotheses,
            "status": PortfolioStatus.DRAFT,
            "generated_from_head_set": HEAD,
            "alternatives_considered": ("another candidate",),
            "next_checks": ("check the records first",),
            "uncertainty_reserve": "unknown",
        }
    )


def _settled(portfolio: HypothesisPortfolioV3, problem: str) -> HypothesisPortfolioV3:
    settled = settle_contract(portfolio, (), problem=problem)
    assert isinstance(settled, HypothesisPortfolioV3)
    return settled


def test_a_korean_question_keeps_korean_sentences_and_records_the_rest() -> None:
    sentence = "독립 재집계에서도 같은 4건이 확인되면 이 후보를 제외한다."
    fragments = ("完全な個別記録", "生成過程", "if both runs match, drop it")
    settled = _settled(
        _portfolio(_hypothesis("h1", (fragments[0], sentence, *fragments[1:]))), KOREAN_QUESTION
    )
    assert settled.hypotheses[0].refutation_conditions == (sentence,)
    dropped = settled.hypotheses[0].dropped
    assert [(d.hypothesis_id, d.reason, d.text) for d in dropped] == [
        ("h1", MISMATCH, item) for item in fragments
    ]
    assert all(isinstance(d, DroppedExpectation) and d.test_id == "" for d in dropped)


def test_a_question_without_hangul_drops_nothing() -> None:
    conditions = ("完全な個別記録", "if both runs match, drop it")
    for problem in (ENGLISH_QUESTION, "", "検出基準が不合格です。"):
        settled = _settled(_portfolio(_hypothesis("h1", conditions)), problem)
        assert settled.hypotheses[0].refutation_conditions == conditions
        assert settled.hypotheses[0].dropped == ()


def test_the_sixth_live_output_loses_exactly_its_two_japanese_fragments() -> None:
    # The refutation conditions of the 2026-10-09 v3 run on SYN-C-DET-RAIN (6th check), as stored.
    first = ("完全な個別記録",)
    second = ("生成過程",)
    third, fourth, fifth = (THIRD,), (FOURTH,), FIFTH
    portfolio = _portfolio(
        _hypothesis("h1", first),
        _hypothesis("h2", second),
        _hypothesis("h3", third),
        _hypothesis("h4", fourth),
        _hypothesis("h5", fifth),
    )
    settled = _settled(portfolio, KOREAN_QUESTION)
    kept = [h.refutation_conditions for h in settled.hypotheses]
    assert kept == [(), (), third, fourth, fifth]
    dropped = [(d.hypothesis_id, d.text) for h in settled.hypotheses for d in h.dropped]
    assert dropped == [("h1", first[0]), ("h2", second[0])]


SCRIPT = "REFUTATION_SCRIPT_MISMATCH"


def test_a_korean_question_drops_a_condition_with_chinese_characters_and_records_it() -> None:
    clean = "독립 재집계에서도 같은 4건이 확인되면 이 후보를 제외한다."
    mixed = "確認된 설정 후보를 바꿔도 오탐 변화가 없으면 해당 설정 후보를 제외한다."
    nohangul = "if both runs match, drop it"
    settled = _settled(_portfolio(_hypothesis("h1", (mixed, clean, nohangul))), KOREAN_QUESTION)
    assert settled.hypotheses[0].refutation_conditions == (clean,)
    assert [(d.reason, d.text) for d in settled.hypotheses[0].dropped] == [
        (MISMATCH, nohangul),
        (SCRIPT, mixed),
    ]


def test_the_script_rule_is_off_when_the_question_itself_has_chinese_characters_or_no_hangul() -> (
    None
):
    mixed = "確認된 설정 후보를 바꿔도 오탐 변화가 없으면 해당 설정 후보를 제외한다."
    for problem in (KOREAN_QUESTION + " 檢證 포함", ENGLISH_QUESTION, ""):
        settled = _settled(_portfolio(_hypothesis("h1", (mixed,))), problem)
        assert settled.hypotheses[0].refutation_conditions == (mixed,)
        assert settled.hypotheses[0].dropped == ()


def test_the_seventh_live_output_loses_its_three_chinese_character_sentences() -> None:
    # The refutation conditions of the 2026-10-09 7th check that carried Chinese characters.
    sentences = (
        "生成 당시 기록에서 다른 결과를 의도했고 작성 오류로 16/20이 되었음이 확인되면 "
        "이 후보를 제외한다.",
        "確認된 설정 후보를 바꿔도 충분한 정밀도에서 오탐 변화가 없으면 해당 설정 후보를 제외한다.",
        "独立 대조에서 중복·제외 오류가 없고 유효 관찰 시간도 정확하면 집계 오류 후보를 제외한다.",
    )
    fine = ("관련 대상·조건의 구성이 같고 충분한 비교에서도 차이가 없으면 이 후보를 제외한다.",)
    portfolio = _portfolio(
        _hypothesis("h1", sentences[:1]),
        _hypothesis("h2", sentences[1:]),
        _hypothesis("h3", fine),
    )
    settled = _settled(portfolio, KOREAN_QUESTION)
    assert [h.refutation_conditions for h in settled.hypotheses] == [(), (), fine]
    dropped = [(d.hypothesis_id, d.reason, d.text) for h in settled.hypotheses for d in h.dropped]
    assert dropped == [
        ("h1", SCRIPT, sentences[0]),
        ("h2", SCRIPT, sentences[1]),
        ("h2", SCRIPT, sentences[2]),
    ]
