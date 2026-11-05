"""CJK retrieval lanes (policy 6.0.0, shadow only): terms, lanes, policy, old lanes kept."""

# ruff: noqa: RUF001
# The Chinese and Japanese samples use their own full-width punctuation on purpose.

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.unit.test_retrieval_selection_oracle import span

from thoth.adapters.behavior.registry import default_behavior_components, evaluate_retrieval
from thoth.application.services.behavior_exposure_compilation import compile_exposure_policy
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    RetrievalBehaviorPolicy,
    RetrievalCjkPolicy,
    RetrievalFusionPolicy,
    RetrievalLanePolicy,
    RetrievalRoutingPolicy,
    is_retrieval_policy,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.lane_scoring import bigram_terms, identifier_terms
from thoth.domain.retrieval_case import evaluation_case_payload
from thoth.domain.retrieval_cjk import (
    CJK_VERSION,
    cjk_bigram_terms,
    cjk_identifier_terms,
    cjk_size,
    cjk_terms,
)
from thoth.domain.retrieval_cjk_policy import CJK_LANE_IDS
from thoth.domain.retrieval_lane import RetrievalRequest, select_retrieval_context

POLICY: TypeAdapter[Any] = TypeAdapter(BehaviorPolicy)

TOKEN_TABLE = [
    ("雨天的探测率", ("雨天", "天的", "的探", "探测", "测率")),  # Simplified Chinese
    ("探測率", ("探測", "測率")),  # Traditional Chinese: other characters, other terms
    ("雨", ("雨",)),  # a one-character run is the character
    (
        "レーダーの検出率",
        ("レー", "ーダ", "ダー", "ーの", "の検", "検出", "出率"),
    ),  # kanji and kana join
    ("ｶﾀｶﾅ", ("カタ", "タカ", "カナ")),  # half-width katakana is folded first
    ("ナ・ノ", ("ナ", "ノ")),  # the katakana middle dot ends a run
    ("雨天，探测", ("雨天", "探测")),  # punctuation ends a run
    ("CFAR门限abc降雨", ("门限", "降雨")),  # Latin neighbours are not part of a run
    ("韓國 레이더 탐지율", ("韓國",)),  # Hanja in Korean text; the Hangul is not CJK
    ("레이더 탐지율", ()),
    ("CFAR 3.5 dB detection_rate", ()),
    ("", ()),
]


@pytest.mark.parametrize(("text", "expected"), TOKEN_TABLE)
def test_cjk_terms_table(text: str, expected: tuple[str, ...]) -> None:
    assert cjk_terms(text) == expected


def test_simplified_and_traditional_chinese_stay_different() -> None:
    assert not set(cjk_terms("探测率")) & set(cjk_terms("探測率"))


@pytest.mark.parametrize(
    ("text", "identifiers", "bigrams"),
    [
        (
            "비 오는 날 CFAR 문턱이 왜 올라가나요? 탐지율 클러터",
            ("오는", "cfar", "문턱이", "올라가나요", "탐지율", "클러터"),
            (
                "오는",
                "cfar",
                "문턱",
                "턱이",
                "올라",
                "라가",
                "가나",
                "나요",
                "탐지",
                "지율",
                "클러",
                "러터",
            ),
        ),
        (
            "Why does the CFAR threshold rise? detection_rate >= 0.90",
            ("detection_rate", "0.90", "why", "does", "the", "cfar", "threshold", "rise"),
            ("why", "does", "the", "cfar", "threshold", "rise", "detection_rate", "0.90"),
        ),
    ],
)
def test_hangul_and_latin_terms_are_what_they_were_and_the_cjk_ones_add_nothing(
    text: str, identifiers: tuple[str, ...], bigrams: tuple[str, ...]
) -> None:
    assert identifier_terms(text) == identifiers
    assert bigram_terms(text) == bigrams
    assert cjk_identifier_terms(text) == identifiers
    assert cjk_bigram_terms(text) == bigrams


def test_a_mixed_text_keeps_the_earlier_terms_first_and_adds_the_cjk_ones_after() -> None:
    text = "探测率 CFAR 탐지율 12dB"
    assert identifier_terms(text) == ("12db", "cfar", "탐지율")
    assert cjk_identifier_terms(text) == ("12db", "cfar", "탐지율", "探测", "测率")
    assert cjk_bigram_terms(text) == ("cfar", "탐지", "지율", "12db", "探测", "测率")


def test_a_han_or_kana_character_counts_for_three_in_the_short_line_rule() -> None:
    assert cjk_size("abc") == 3
    assert cjk_size("探测率") == 9
    assert cjk_size("CFAR降雨") == 10


def spans(*texts: str) -> tuple[EvidenceSpan, ...]:
    return tuple(span(f"s{i}", text, line=i + 1) for i, text in enumerate(texts))


ZH = spans(
    "雷达在雨天的探测率为0.80，低于要求的0.90。",
    "原因分析：降雨导致的杂波增强，使恒虚警门限（CFAR）抬高，微弱目标被漏检。",
    "天线罩的材料为玻璃纤维，重量约为12公斤。",
    "测试于2026年3月在海边试验场进行，共进行了20次飞行。",
    "雾天的虚警率为每分钟0.5次，满足要求。该指标符合合同。",
)
JA = spans(
    "レーダーの雨天時の検出率は0.80で、要求値の0.90を下回った。",
    "原因分析：降雨によるクラッターの増加でCFARのしきい値が上昇し、目標を見逃した。",
    "レドームの材質はガラス繊維で、重量は約12キログラムである。",
    "試験は2026年3月に海岸の試験場で、飛行20回を実施した。",
    "霧の日の誤警報率は毎分0.5回で、要求を満たしている。この値は契約に適合する。",
)
OLD = {
    "LEXICAL_SUBSTRING_CJK": RetrievalBehaviorPolicy(max_spans=2),
    "GROUPED_BM25_CJK": RetrievalLanePolicy.model_validate(
        {"max_spans": 2, "lanes": [{"lane_id": "GROUPED_BM25"}]}
    ),
    "CHAR_BIGRAM_CJK": RetrievalLanePolicy.model_validate(
        {"max_spans": 2, "lanes": [{"lane_id": "CHAR_BIGRAM"}]}
    ),
}


def picked(question: str, evidence: tuple[EvidenceSpan, ...], policy: object) -> list[str]:
    selection = select_retrieval_context(
        RetrievalRequest(question, (), evidence),
        policy,  # type: ignore[arg-type]
    )
    return [s.span_id.removeprefix("span:") for s in selection.selected]


@pytest.mark.parametrize("lane", CJK_LANE_IDS)
@pytest.mark.parametrize(
    ("evidence", "question", "wanted"),
    [
        (ZH, "为什么雨天的探测率低于标准？", {"s0"}),
        (ZH, "降雨为什么使杂波增强", {"s1"}),
        (JA, "なぜ雨天時の検出率は基準より低いのですか", {"s0"}),
        (JA, "降雨によるクラッターの増加", {"s1"}),
        (JA, "レドーム", {"s2"}),  # only kana
        (JA, "クラッター", {"s1"}),
    ],
)
def test_each_cjk_lane_finds_the_paragraph_a_pure_chinese_or_japanese_question_is_about(
    lane: str, evidence: tuple[EvidenceSpan, ...], question: str, wanted: set[str]
) -> None:
    got = picked(question, evidence, RetrievalCjkPolicy(max_spans=2, lane_id=lane))  # type: ignore[arg-type]
    assert wanted <= set(got)
    assert got[0] in wanted


@pytest.mark.parametrize(
    ("lane", "question"),
    [
        ("GROUPED_BM25_CJK", "为什么雨天的探测率低于标准？"),
        ("CHAR_BIGRAM_CJK", "降雨为什么使杂波增强"),
    ],
)
def test_the_earlier_ranked_lanes_find_nothing_for_the_same_questions(
    lane: str, question: str
) -> None:
    assert picked(question, ZH, OLD[lane]) == []


def test_a_short_chinese_line_is_not_dropped_as_chrome_by_the_cjk_substring_lane() -> None:
    short = span("short", "雨天探测率低于标准值。", line=9)
    evidence = (*ZH, short)
    question = "探测率低于标准值"
    assert "short" not in picked(question, evidence, OLD["LEXICAL_SUBSTRING_CJK"])
    got = picked(
        question, evidence, RetrievalCjkPolicy(max_spans=2, lane_id="LEXICAL_SUBSTRING_CJK")
    )
    assert got[0] == "short"


def test_the_cjk_lanes_are_reached_only_through_a_6_0_0_policy() -> None:
    request = RetrievalRequest("探测率", (), ZH)
    for lane in CJK_LANE_IDS:
        with pytest.raises(ValueError, match="RETRIEVAL_LANE_CJK_NEEDS_CJK_POLICY"):
            select_retrieval_context(request, RetrievalBehaviorPolicy(), lane)
        with pytest.raises(ValueError, match="RETRIEVAL_LANE_ID_NOT_ALLOWED_FOR_CJK"):
            select_retrieval_context(request, RetrievalCjkPolicy(lane_id=lane), lane)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            RetrievalLanePolicy.model_validate({"lanes": [{"lane_id": lane}]})
    with pytest.raises(ValidationError):
        RetrievalFusionPolicy.model_validate(
            {
                "lanes": [
                    {"lane_id": "GROUPED_BM25_CJK", "depth": 3},
                    {"lane_id": "CHAR_BIGRAM", "depth": 3},
                ]
            }
        )


def test_a_lane_version_other_than_the_registered_one_is_refused() -> None:
    with pytest.raises(ValidationError):
        RetrievalCjkPolicy.model_validate({"lane_id": "CHAR_BIGRAM_CJK", "lane_version": "2.0.0"})


def test_the_policy_names_its_lane_and_is_shadow_only() -> None:
    with pytest.raises(ValidationError):
        RetrievalCjkPolicy.model_validate({})
    with pytest.raises(ValidationError):
        RetrievalCjkPolicy.model_validate({"lane_id": "GROUPED_BM25"})
    with pytest.raises(ValidationError):
        RetrievalCjkPolicy.model_validate({"lane_id": "CHAR_BIGRAM_CJK", "exposure": "CANARY"})
    policy = RetrievalCjkPolicy(lane_id="CHAR_BIGRAM_CJK")
    assert policy.model_dump_json() == (
        '{"kind":"RETRIEVAL_POLICY","version":"6.0.0","max_spans":160,"character_budget":50000,'
        '"exposure":"SHADOW_ONLY","lane_id":"CHAR_BIGRAM_CJK","lane_version":"1.0.0"}'
    )
    assert is_retrieval_policy(policy)
    parsed = POLICY.validate_python(policy.model_dump())
    assert type(parsed) is RetrievalCjkPolicy
    assert parsed == policy


def test_the_earlier_policies_keep_their_serialization() -> None:
    assert RetrievalBehaviorPolicy().model_dump_json() == (
        '{"kind":"RETRIEVAL_POLICY","version":"2.0.0","max_spans":160,"character_budget":50000}'
    )
    assert RetrievalLanePolicy().model_dump_json() == (
        '{"kind":"RETRIEVAL_POLICY","version":"3.0.0","max_spans":160,"character_budget":50000,'
        '"lanes":[{"lane_id":"LEXICAL_SUBSTRING_V1","lane_version":"1.0.0"}]}'
    )
    assert RetrievalRoutingPolicy().model_dump()["version"] == "5.0.0"


def test_the_handler_is_shadow_only_and_never_a_default() -> None:
    handler = default_behavior_components().resolve(BehaviorArtifactKind.RETRIEVAL_POLICY, "6.0.0")
    data = RetrievalCjkPolicy(lane_id="GROUPED_BM25_CJK").model_dump()
    assert compile_exposure_policy(handler, data, "SHADOW").version == "6.0.0"
    for stage in ("CANARY", "BASELINE"):
        with pytest.raises(BehaviorPolicyError, match="RETRIEVAL_CJK_SHADOW_ONLY"):
            compile_exposure_policy(handler, data, stage)
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        handler.default_policy()
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        handler.compile({"lane_id": "GROUPED_BM25"})


def test_evaluation_records_the_lane_and_its_term_rule() -> None:
    policy = RetrievalCjkPolicy(max_spans=2, lane_id="GROUPED_BM25_CJK")
    handler = default_behavior_components().resolve(BehaviorArtifactKind.RETRIEVAL_POLICY, "6.0.0")
    payload = evaluation_case_payload(RetrievalRequest("为什么雨天的探测率低于标准？", (), ZH))
    out = handler.evaluate(policy, payload)
    assert out["selected_refs"][0] == "span:s0"  # type: ignore[index]
    assert dict(out["lane"])["terms"] == CJK_VERSION  # type: ignore[arg-type]
    assert out == evaluate_retrieval(policy, payload)
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_KIND_MISMATCH"):
        handler.evaluate(RetrievalBehaviorPolicy(), payload)
