"""Korean short search words find source content without widening other token rules."""

import hashlib
import unicodedata

import pytest

from thoth.application.services.research_retrieval import lexical_candidates, search_terms
from thoth.domain.artifact import SourceLocator
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan


def span(identifier: str, text: str, *, cutoff: CutoffState = CutoffState.ELIGIBLE) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=identifier,
        project_id="project:terms",
        artifact_id="artifact:terms",
        source_version_id="version:terms",
        locator=SourceLocator(line=1),
        exact_text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        extraction_method="test",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.INFORMAL,
        verification_state=VerificationState.SCHEMA_VALID,
        cutoff_state=cutoff,
    )


def test_korean_short_words_reach_measurements_beside_an_identifier_match() -> None:
    source = (
        span(
            "span:reference", "REF-21 is a reference for this comparison, without measured values."
        ),
        span(
            "span:temperature",
            "온도 변화에 따른 압력 측정값은 대조 조건을 고정한 상태에서 기록했다.",
        ),
        span(
            "span:unrelated",
            "Only unrelated calendar dates and meeting locations are recorded here.",
        ),
    )
    selected = lexical_candidates("REF-21 온도 압력", (), source)
    assert {s.span_id for s in selected} == {"span:reference", "span:temperature"}


def test_expanded_korean_query_reaches_a_result_without_matching_the_initial_id() -> None:
    source = (
        span(
            "span:reference",
            "CASE-09 identifies the source comparison but contains no observation.",
        ),
        span(
            "span:observation",
            "교육 대상의 독서 평가 결과는 동일한 문항을 이용한 비교 조건에서 기록했다.",
        ),
    )
    selected = lexical_candidates("CASE-09", ("교육 독서 평가",), source)
    assert {s.span_id for s in selected} == {"span:reference", "span:observation"}


def test_only_whole_two_syllable_korean_words_are_newly_admitted() -> None:
    assert search_terms(
        "강우 신호 실험 결과 탐지 설정 온도 압력 독서 교육 AI ms 12 5G 温度 かな A가 가1 ㄱㄴ"
    ) == {"강우", "신호", "실험", "결과", "탐지", "설정", "온도", "압력", "독서", "교육"}


def test_function_words_do_not_introduce_topic_matches() -> None:
    assert (
        search_terms(
            "경우 관련 때문 또는 대한 대해 따라 또한 위한 위해 이번 이런 저런 통해 에서 으로"
        )
        == set()
    )
    source = (
        span("span:anchor", "ANCHOR is the actual topic with a sufficiently long source sentence."),
        span(
            "span:noise",
            "이번 관련 경우 등을 나열한 이 문장은 질문의 주제에 대한 측정 결과가 아니다.",
        ),
    )
    assert [s.span_id for s in lexical_candidates("ANCHOR 이번 관련 경우", (), source)] == [
        "span:anchor"
    ]


def test_legacy_long_tokens_numbers_and_identifiers_stay_intact() -> None:
    assert search_terms("그리고 강우에서 신호는 ABC A-1 0.90 123 SYN-C-DET-RAIN 1/2 온도-A") == {
        "그리고",
        "강우에서",
        "신호는",
        "abc",
        "a-1",
        "0.90",
        "123",
        "syn-c-det-rain",
        "1/2",
        "온도-a",
    }
    assert "강우" not in search_terms("강우에서")
    assert "신호" not in search_terms("신호는")


def test_existing_unicode_normalization_is_preserved() -> None:
    fullwidth = " \uff21\uff22\uff23 \uff11\uff12\uff13"
    assert search_terms(unicodedata.normalize("NFD", "온도 압력") + fullwidth) == {
        "온도",
        "압력",
        "abc",
        "123",
    }


@pytest.mark.parametrize("cutoff", [CutoffState.AFTER_CUTOFF, CutoffState.PROHIBITED_CONTEXT])
def test_korean_terms_and_pins_do_not_bypass_source_eligibility(cutoff: CutoffState) -> None:
    allowed = span(
        "span:allowed", "온도 측정값은 동일한 비교 조건에서 기록한 유효한 연결 자료이다."
    )
    denied = span(
        "span:denied",
        "온도 측정값이 있어도 이 자료의 사용 범위는 현재 요청에서 허용되지 않는다.",
        cutoff=cutoff,
    )
    selected = lexical_candidates("온도", (), (denied, allowed), (denied.span_id,))
    assert selected == (allowed,)


def test_many_korean_matches_keep_existing_count_and_character_limits() -> None:
    source = tuple(span(f"span:{i:03}", "온도 " + "x" * 497) for i in range(200))
    selected = lexical_candidates("온도", (), source, ("span:199",))
    assert selected[0].span_id == "span:199"
    assert len(selected) == 100 and sum(len(s.exact_text) for s in selected) == 50000
    shorter = tuple(span(f"span:{i:03}", "온도 " + "x" * 27) for i in range(200))
    assert len(lexical_candidates("온도", (), shorter)) == 160
