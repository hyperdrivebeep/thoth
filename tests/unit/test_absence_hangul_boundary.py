"""Hangul source-side suffixes must not hide declared literal witnesses."""

import pytest
from tests.unit.test_absence_claim_checks import assessed, proposal, source

from thoth.application.services.research_absence_checks import literal_present
from thoth.domain.absence_claim import (
    CHECKER_VERSION,
    HANGUL_SOURCE_BOUNDARY_VERSION,
    HANGUL_SOURCE_SUFFIXES,
    AbsenceAssessments,
    absence_check_digest,
)
from thoth.domain.canonical import canonical_payload, domain_digest

EXPECTED_SUFFIXES = [
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "에서",
    "에게",
    "의",
    "와",
    "과",
    "도",
    "만",
    "로",
    "으로",
    "까지",
    "부터",
    "보다",
    "처럼",
    "에는",
    "에서는",
    "으로는",
    "로는",
    "과는",
    "와는",
    "이나",
    "나",
    "이며",
    "이고",
    "이다",
    "입니다",
    "이라",
    "이라고",
    "라고",
]


@pytest.mark.parametrize(
    "target,text",
    [
        ("버전 제목", "버전 제목은 69행에 있다"),
        ("반복 측정", "반복 측정을 하지 않았다"),
    ],
)
def test_reviewed_hangul_boundary_regressions(target: str, text: str) -> None:
    assert literal_present(target, text)


@pytest.mark.parametrize("suffix", EXPECTED_SUFFIXES)
def test_exact_versioned_source_suffix_is_accepted(suffix: str) -> None:
    assert literal_present("보고서", f"보고서{suffix} 다음 내용")


@pytest.mark.parametrize(
    "target,text,expected",
    [
        ("가", "가격", False),
        ("가", "가격은 오릅니다", False),
        ("보고서", "원보고서", False),
        ("버전 제목", "구버전 제목은 69행", False),
        ("반복 측정", "반복 측정값을 기록했다", False),
        ("보고서", "보고서은것", False),
        ("보고서", "보고서으로는것", False),
        ("보고서", "보고서이라고는", False),
        ("보고서", "보고서엔 없다", False),
        ("보고서", "보고서", True),
        ("보고서", "보고서 내용", True),
        ("보고서", "보고서:내용", True),
        ("보고서", "자료:보고서입니다.", True),
        ("보고서", "(보고서)", True),
        ("반복 측정", "반복 측정을3회 했다", True),
        ("보고서", "보고서A", False),
        ("보고서", "보고서2", False),
        ("보고서1", "A보고서1", False),
        ("보고서1", "자료:보고서1", False),
        ("보고서", "보고서엔 없고 보고서는 있다", True),
    ],
)
def test_longer_words_unlisted_suffixes_and_real_boundaries(
    target: str, text: str, expected: bool
) -> None:
    assert literal_present(target, text) is expected


def test_boundary_policy_is_explicit_and_versioned() -> None:
    assert CHECKER_VERSION == "literal-absence:1.1.0"
    assert HANGUL_SOURCE_BOUNDARY_VERSION == "hangul-source-suffix:1.0.0"
    assert frozenset(EXPECTED_SUFFIXES) == HANGUL_SOURCE_SUFFIXES


def test_korean_claim_is_found_in_both_scopes_without_changing_target() -> None:
    data = source("# 자료\n\n버전 제목은 69행에 있다\n\n반복 측정을 하지 않았다\n")
    original = proposal("버전 제목")
    before = canonical_payload(original)
    result = assessed(original, data, data)
    assert result.checker_version == CHECKER_VERSION
    assert result.items[0].a_result.outcome == result.items[0].b_result.outcome == "FOUND"
    assert result.items[0].effective == "FOUND_IN_MODEL_INPUT"
    assert result.items[0].claim.target == "버전 제목"
    assert canonical_payload(original) == before


def test_new_checker_digest_differs_even_when_english_outcomes_are_unchanged() -> None:
    result = assessed(proposal(), source(), source())
    raw = result.model_dump(mode="json")
    raw["checker_version"] = "literal-absence:1.0.0"
    for item in raw["items"]:
        old_payload = {
            key: item[key]
            for key in (
                "claim_id",
                "claim_digest",
                "origin_stage_ref",
                "origin_pointer",
                "origin_bound",
                "a_result",
                "b_result",
                "effective",
                "warning_code",
            )
        }
        legacy = domain_digest("ABSENCE_CHECK", "1.0.0", canonical_payload(old_payload))
        assert item["check_digest"] != legacy
        item["check_digest"] = legacy
    old = AbsenceAssessments.model_validate(raw)
    before = canonical_payload(old)
    assert old.checker_version == "literal-absence:1.0.0"
    assert old.items[0].a_result == result.items[0].a_result
    assert old.items[0].b_result == result.items[0].b_result
    assert canonical_payload(AbsenceAssessments.model_validate_json(before)) == before
    mixed = {**raw, "checker_version": CHECKER_VERSION}
    with pytest.raises(ValueError, match="ABSENCE_CHECK_DIGEST_MISMATCH"):
        AbsenceAssessments.model_validate(mixed)


def test_suffix_policy_contents_participate_in_new_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    from thoth.domain import absence_claim

    fields = assessed(proposal(), source(), source()).items[0].model_dump()
    before = absence_check_digest(fields)
    monkeypatch.setattr(absence_claim, "HANGUL_SOURCE_SUFFIXES", HANGUL_SOURCE_SUFFIXES | {"엔"})
    assert absence_check_digest(fields) != before
