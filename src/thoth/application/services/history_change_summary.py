"""What changed between two versions of one record, in words, from the existing revision diff."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TypeGuard, cast

from thoth.application.services.revision_diff import semantic_diff
from thoth.domain.research_history import ChangeFlag, ChangeLine, ChangeSummary

MAX_LINES = 8
MAX_TECHNICAL = 20
MAX_TEXT = 2_000  # a text change is shown in full so it can be read; the UI shortens it in lists
_VOLATILE = frozenset(
    {
        "revision_digest",
        "supersedes_revision_digest",
        "parent_revision_digest",
        "created_at",
        "receipt_ref",
        "schema_version",
        "reviews",
    }
)
_LABELS = {
    "statement": "가설 문장",
    "observed_problem": "관찰된 문제",
    "evidence_refs": "뒷받침 근거",
    "counterevidence_refs": "반박 근거",
    "empirical_appraisal": "검증 상태",
    "development_stage": "발전 단계",
    "specification": "행동 내용",
    "risk_tier": "위험 등급",
    "scope": "적용 조건",
    "cutoff_at": "기준시점",
    "authority_status": "출처 권위",
    "support_status": "근거 충분성",
    "assertion": "기억 내용",
    "transition": "검토 결과",
    "decision_state": "판단 상태",
    "authorization_state": "승인 상태",
}
_STATUS = frozenset(
    {
        "empirical_appraisal",
        "development_stage",
        "decision_state",
        "validation_state",
        "derived_status",
        "proposal_state",
        "policy_state",
        "authorization_state",
        "transition",
        "support_status",
        "lifecycle",
        "state",
    }
)
_CITATION = frozenset({"evidence_refs", "counterevidence_refs", "source_ref", "source_refs"})
_TIME = frozenset({"cutoff_at", "valid_until", "valid_from", "expires_at", "observed_at"})
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NEGATION = re.compile(
    r"않|아니|없|못하|못한|불가|\b(?:not|no|never|cannot|without)\b|n't", re.IGNORECASE
)
_TECHNICAL_SUFFIX = ("_id", "_ref", "_refs", "_digest")


def summarize_change(
    before: Mapping[str, object], after: Mapping[str, object]
) -> ChangeSummary | None:
    """Field-level summary of a change, or None when nothing but identity or timestamps moved."""

    changes = semantic_diff(_stable(before), _stable(after))
    fields: list[str] = []
    for change in changes:
        field = change.path.strip("/").split("/", 1)[0].replace("~1", "/").replace("~0", "~")
        if field and field not in fields:
            fields.append(field)
    if not fields:
        return None
    lines: list[ChangeLine] = []
    shown: list[str] = []
    technical: list[ChangeLine] = []
    other = 0
    for field in fields:
        old, new = before.get(field), after.get(field)
        if field == "generation_details":
            estimates = _estimate_lines(old, new)
            lines.extend(estimates)
            if _without_estimates(old) == _without_estimates(new):
                continue
        elif field in _LABELS:
            lines.append(ChangeLine(label=_LABELS[field], **_pair(old, new)))
            shown.append(field)
            continue
        if not field.endswith(_TECHNICAL_SUFFIX):
            other += 1
        technical.append(ChangeLine(label=field, **_pair(old, new)))
    flags = _flags(shown, before, after)
    return ChangeSummary(
        lines=tuple(lines[:MAX_LINES]),
        more=max(0, len(lines) - MAX_LINES),
        flags=flags,
        text_diff_recommended=bool(flags),
        other=other,
        technical=tuple(technical[:MAX_TECHNICAL]),
    )


def _pair(old: object, new: object) -> dict[str, str]:
    before_text, after_text = _texts(old, new)
    return {"before": before_text, "after": after_text}


_BANDS = {
    "TIME": {"LOW": "짧음", "MEDIUM": "보통", "HIGH": "김", "UNKNOWN": "미확인"},
    "COST_EFFORT": {"LOW": "낮음", "MEDIUM": "중간", "HIGH": "높음", "UNKNOWN": "미확인"},
}
_DIMENSIONS = {"TIME": "시간 추정", "COST_EFFORT": "비용·노력 추정"}
_ESTIMATORS = {"AI": "AI", "HUMAN": "사람", "RULE": "규칙"}


def _estimates(details: object) -> list[Mapping[str, object]]:
    raw = (
        cast(Mapping[str, object], details).get("effort_estimates")
        if isinstance(details, Mapping)
        else None
    )
    return (
        [item for item in cast(list[object], raw) if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )  # pyright: ignore[reportUnknownVariableType]


def _current_estimate(details: object, dimension: str) -> str:
    """The estimate a reader would see: the latest person's, else the latest AI's."""

    found = [item for item in _estimates(details) if item.get("dimension") == dimension]
    human = [item for item in found if item.get("estimator_type") == "HUMAN"]
    chosen = (human or found or [None])[-1]
    if chosen is None:
        return "없음"
    band = _BANDS[dimension].get(str(chosen.get("band")), str(chosen.get("band")))
    return f"{band}({_ESTIMATORS.get(str(chosen.get('estimator_type')), '기타')})"


def _estimate_lines(old: object, new: object) -> list[ChangeLine]:
    lines: list[ChangeLine] = []
    for dimension, label in _DIMENSIONS.items():
        before_text, after_text = (
            _current_estimate(old, dimension),
            _current_estimate(new, dimension),
        )
        if before_text != after_text:
            lines.append(ChangeLine(label=label, before=before_text, after=after_text))
    return lines


def _without_estimates(details: object) -> object:
    if not isinstance(details, Mapping):
        return details
    return {
        key: value
        for key, value in cast(Mapping[str, object], details).items()
        if key != "effort_estimates"
    }


def summarize_user_correction(before: str, after: str) -> ChangeSummary:
    """A user's correction of a memory: the memory as it was next to the corrected text."""

    flags = _flags(["correction"], {"correction": before}, {"correction": after})
    line = ChangeLine(label="사용자 정정", before=_short(before), after=_short(after))
    return ChangeSummary(lines=(line,), flags=flags, text_diff_recommended=bool(flags))


def _stable(value: Mapping[str, object]) -> dict[str, object]:
    return {
        key: child
        for key, child in value.items()
        if key not in _VOLATILE and not key.endswith("_revision_id")
    }


def _texts(before: object, after: object) -> tuple[str, str]:
    if _is_text_list(before) and _is_text_list(after):
        old, new = list(before), list(after)
        added = len([item for item in new if item not in old])
        removed = len([item for item in old if item not in new])
        return f"{len(old)}개", f"{len(new)}개 (추가 {added} · 삭제 {removed})"
    return _short(before), _short(after)


def _is_text_list(value: object) -> TypeGuard[list[str] | tuple[str, ...]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) for item in cast(list[object], value)
    )


def _short(value: object) -> str:
    if value is None:
        return "없음"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _flags(
    fields: list[str], before: Mapping[str, object], after: Mapping[str, object]
) -> tuple[ChangeFlag, ...]:
    found: set[ChangeFlag] = set()
    for field in fields:
        old, new = before.get(field), after.get(field)
        if field in _STATUS:
            found.add("STATUS")
        if field in _CITATION:
            found.add("CITATION")
        if "authority" in field:
            found.add("AUTHORITY")
        if field in _TIME:
            found.add("TIME")
        if field in _CITATION or field in _STATUS or field in _TIME:
            continue
        if field.endswith(_TECHNICAL_SUFFIX):
            continue
        for a, b in ((old, new),):
            if isinstance(a, str) or isinstance(b, str):
                left, right = a if isinstance(a, str) else "", b if isinstance(b, str) else ""
                if _NUMBER.findall(left) != _NUMBER.findall(right):
                    found.add("NUMBER")
                if len(_NEGATION.findall(left)) != len(_NEGATION.findall(right)):
                    found.add("NEGATION")
                if _DATE.findall(left) != _DATE.findall(right):
                    found.add("TIME")
            elif isinstance(a, int | float) or isinstance(b, int | float):
                found.add("NUMBER")
    order: tuple[ChangeFlag, ...] = (
        "NUMBER",
        "NEGATION",
        "STATUS",
        "CITATION",
        "AUTHORITY",
        "TIME",
    )
    return tuple(flag for flag in order if flag in found)
