"""Time and cost-effort bands: meanings, boundary rules and safe handling.

Bands are ordinal labels. They are never stored as numbers, summed, averaged or sorted, and an
UNKNOWN band is a separate "needs confirmation" group rather than the cheapest option.
"""

from __future__ import annotations

from thoth.domain.action import EffortEstimateDraft, OrdinalEstimate
from thoth.domain.base import DomainModel

EFFORT_BAND_PROFILE_ID = "effort-band-profile"
EFFORT_BAND_PROFILE_VERSION = "1.0.0"
AI_ESTIMATOR_REF = "ACTION_PLANNER"
_DIMENSIONS = ("TIME", "COST_EFFORT")
_KNOWN_BANDS = ("LOW", "MEDIUM", "HIGH")


class EffortBandDefinition(DomainModel):
    dimension: str
    band: str
    label: str
    criterion: str
    examples: str


class EffortBandProfile(DomainModel):
    profile_id: str
    version: str
    definitions: tuple[EffortBandDefinition, ...]
    absolute_triggers: tuple[str, ...]
    boundary_rules: tuple[str, ...]


EFFORT_BAND_PROFILE_V1 = EffortBandProfile(
    profile_id=EFFORT_BAND_PROFILE_ID,
    version=EFFORT_BAND_PROFILE_VERSION,
    definitions=(
        EffortBandDefinition(
            dimension="TIME",
            band="LOW",
            label="짧음",
            criterion=(
                "THOTH 안에서 같은 작업 흐름 또는 당일 안에 끝낼 수 있고 외부 응답 대기가 없음"
            ),
            examples="연결 자료 다시 대조, 기존 데이터 재분석, 문서 안 위치 확인",
        ),
        EffortBandDefinition(
            dimension="TIME",
            band="MEDIUM",
            label="보통",
            criterion="외부 담당자 회신·내부 협의·예약이 필요하며 보통 며칠 단위",
            examples="담당자에게 자료 요청, 다른 팀 확인, 기존 장비로 추가 분석",
        ),
        EffortBandDefinition(
            dimension="TIME",
            band="HIGH",
            label="김",
            criterion="현장·시험·조달·계약·장비 일정이 필요하거나 보통 몇 주 이상",
            examples="재시험, 재측정, 시제품 제작, 외부 시험기관 의뢰",
        ),
        EffortBandDefinition(
            dimension="TIME",
            band="UNKNOWN",
            label="미확인",
            criterion="기간을 판단할 근거가 없음",
            examples="담당자·장비·자료 가용성을 아직 확인하지 않음",
        ),
        EffortBandDefinition(
            dimension="COST_EFFORT",
            band="LOW",
            label="낮음",
            criterion="새 지출·구매·장비·외부 인력이 없고 담당자 한 명의 짧은 검토로 가능",
            examples="연결 자료 다시 대조, 기존 데이터 재분석, 문서 안 위치 확인",
        ),
        EffortBandDefinition(
            dimension="COST_EFFORT",
            band="MEDIUM",
            label="중간",
            criterion=(
                "기존 조직 자원 안에서 여러 사람의 시간이나 소규모 운영 조정이 필요하지만 "
                "새 예산 승인·조달은 없음"
            ),
            examples="담당자에게 자료 요청, 다른 팀 확인, 기존 장비로 추가 분석",
        ),
        EffortBandDefinition(
            dimension="COST_EFFORT",
            band="HIGH",
            label="높음",
            criterion=(
                "새 예산 승인, 구매·계약, 외부 기관, 장비 사용, 현장 운영 중 하나 이상이 필요"
            ),
            examples="재시험, 재측정, 시제품 제작, 외부 시험기관 의뢰",
        ),
        EffortBandDefinition(
            dimension="COST_EFFORT",
            band="UNKNOWN",
            label="미확인",
            criterion="비용·품을 판단할 근거가 없음",
            examples="담당자·장비·자료 가용성을 아직 확인하지 않음",
        ),
    ),
    absolute_triggers=(
        "새 조달이나 현장 시험이 있으면 금액이 작아 보여도 HIGH 후보",
        "외부 담당자 회신이 필요하면 TIME은 LOW가 아님",
    ),
    boundary_rules=(
        "작업 유형의 절대 trigger를 먼저 적용한다",
        "당일·며칠·몇 주는 이해를 돕는 참고 표현이며 SLA나 경계 값이 아니다",
        "시간과 비용은 독립적으로 매긴다. 짧음·높음, 김·낮음 조합도 허용한다",
        "판단할 근거가 없으면 UNKNOWN으로 두고 LOW로 취급하지 않는다",
        "추정자가 다르면 평균내지 않고 각각 남긴다",
    ),
)


def effort_band_guidance(profile: EffortBandProfile = EFFORT_BAND_PROFILE_V1) -> str:
    """Prompt text for the action planner, generated from the profile so the two cannot drift."""

    rows = "; ".join(
        f"{item.dimension} {item.band} ({item.label}): {item.criterion}"
        for item in profile.definitions
    )
    return (
        f"Effort bands (profile {profile.profile_id} {profile.version}): {rows}. "
        f"Absolute triggers: {'; '.join(profile.absolute_triggers)}. "
        f"Rules: {'; '.join(profile.boundary_rules)}."
    )


def ai_estimates_from_draft(drafts: tuple[EffortEstimateDraft, ...]) -> tuple[OrdinalEstimate, ...]:
    """Normalize model-written estimates: one per dimension, unsupported values become UNKNOWN."""

    seen: set[str] = set()
    result: list[OrdinalEstimate] = []
    for item in drafts:
        dimension = item.dimension.strip().upper()
        if dimension not in _DIMENSIONS or dimension in seen:
            continue
        seen.add(dimension)
        band = item.band.strip().upper()
        basis = item.basis_text.strip()
        if band not in _KNOWN_BANDS or not basis:
            band = "UNKNOWN"
        result.append(
            OrdinalEstimate.model_validate(
                {
                    "dimension": dimension,
                    "band": band,
                    "estimator_type": "AI",
                    "estimator_ref": AI_ESTIMATOR_REF,
                    "basis_text": basis,
                    "assumptions": item.assumptions,
                    "profile_id": EFFORT_BAND_PROFILE_ID,
                    "profile_version": EFFORT_BAND_PROFILE_VERSION,
                }
            )
        )
    return tuple(result)


def partition_by_band(
    estimates: tuple[OrdinalEstimate, ...],
) -> dict[str, tuple[OrdinalEstimate, ...]]:
    """Group without ranking. UNKNOWN is its own NEEDS_CONFIRMATION group."""

    groups: dict[str, tuple[OrdinalEstimate, ...]] = {
        key: tuple(item for item in estimates if item.band == key) for key in _KNOWN_BANDS
    }
    groups["NEEDS_CONFIRMATION"] = needs_confirmation(estimates)
    return groups


def needs_confirmation(estimates: tuple[OrdinalEstimate, ...]) -> tuple[OrdinalEstimate, ...]:
    return tuple(item for item in estimates if item.band == "UNKNOWN")
