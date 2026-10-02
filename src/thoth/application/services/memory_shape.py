"""What kind of record a memory points at, read from the record's own fields.

One place decides this, so candidate creation, recall and the readable summary agree: a
hypothesis portfolio, an action plan and an action portfolio are containers that group other
records, and they are never a memory of their own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import cast

MAX_LINE = 200
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


class RecordShape(StrEnum):
    HYPOTHESIS = "HYPOTHESIS"
    ACTION = "ACTION"
    OUTCOME = "OUTCOME"
    HYPOTHESIS_PORTFOLIO = "HYPOTHESIS_PORTFOLIO"
    ACTION_PLAN = "ACTION_PLAN"
    ACTION_PORTFOLIO = "ACTION_PORTFOLIO"
    OTHER = "OTHER"


CONTAINER_SHAPES = frozenset(
    {RecordShape.HYPOTHESIS_PORTFOLIO, RecordShape.ACTION_PLAN, RecordShape.ACTION_PORTFOLIO}
)


def first_line(text: str) -> str:
    first = _SENTENCE_END.split(" ".join(text.split()), maxsplit=1)[0]
    return first if len(first) <= MAX_LINE else first[: MAX_LINE - 1] + "…"


def text_of(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _field(value: object, name: str) -> object:
    return cast(Mapping[str, object], value).get(name) if isinstance(value, Mapping) else None


def _count(content: Mapping[str, object], *names: str) -> int | None:
    for name in names:
        value = content.get(name)
        if isinstance(value, (list, tuple)):
            return len(cast(list[object], value))
    return None


def record_shape(content: Mapping[str, object]) -> RecordShape:
    if text_of(content.get("statement")) is not None:
        return RecordShape.HYPOTHESIS
    if "action_id" in content and "specification" in content:  # read before its portfolio link
        return RecordShape.ACTION
    if "plan_id" in content:
        return RecordShape.ACTION_PLAN
    if "portfolio_id" in content and ("hypothesis_refs" in content or "hypotheses" in content):
        return RecordShape.HYPOTHESIS_PORTFOLIO
    if "portfolio_id" in content and "action_refs" in content:
        return RecordShape.ACTION_PORTFOLIO
    if text_of(content.get("interpretation")) is not None:
        return RecordShape.OUTCOME
    return RecordShape.OTHER


def is_container(content: Mapping[str, object]) -> bool:
    return record_shape(content) in CONTAINER_SHAPES


def container_line(content: Mapping[str, object], shape: RecordShape) -> str | None:
    if shape == RecordShape.ACTION_PLAN:
        count = _count(content, "selected_action_refs", "steps")
        return "행동 계획" if count is None else f"행동 {count}개 계획"
    if shape == RecordShape.HYPOTHESIS_PORTFOLIO:
        count = _count(content, "hypothesis_refs", "hypotheses")
        return "가설 묶음" if count is None else f"가설 {count}개 묶음"
    if shape == RecordShape.ACTION_PORTFOLIO:
        count = _count(content, "action_refs")
        return "행동 묶음" if count is None else f"행동 {count}개 묶음"
    return None


def member_keys(content: Mapping[str, object]) -> frozenset[str]:
    """Ledger keys of the records a container groups, for telling a member from a stranger."""

    shape = record_shape(content)
    if shape == RecordShape.HYPOTHESIS_PORTFOLIO:
        prefix, names = "HYPOTHESIS:", ("hypothesis_refs",)
    elif shape == RecordShape.ACTION_PLAN:
        prefix, names = "ACTION:", ("selected_action_refs",)
    elif shape == RecordShape.ACTION_PORTFOLIO:
        prefix, names = "ACTION:", ("action_refs",)
    else:
        return frozenset()
    found: set[str] = set()
    for name in names:
        refs = content.get(name)
        if isinstance(refs, (list, tuple)):
            found.update(
                f"{prefix}{ref}" for ref in cast(list[object], refs) if isinstance(ref, str)
            )
    return frozenset(found)


def _action_title(content: Mapping[str, object]) -> str | None:
    spec = content.get("specification")
    description = _field(spec, "description")
    return text_of(_field(spec, "title")) or text_of(spec if isinstance(spec, str) else description)


def key_fields(content: Mapping[str, object]) -> dict[str, str]:
    """The fields whose difference between two versions of one record means a real change."""

    shape = record_shape(content)
    fields: dict[str, str | None]
    if shape == RecordShape.HYPOTHESIS:
        fields = {
            "statement": text_of(content.get("statement")),
            "empirical_appraisal": text_of(content.get("empirical_appraisal")),
        }
    elif shape == RecordShape.ACTION:
        fields = {
            "title": _action_title(content),
            "purpose": text_of(content.get("primary_purpose")),
        }
    elif shape == RecordShape.OUTCOME:
        fields = {"interpretation": text_of(content.get("interpretation"))}
    else:
        fields = {}
    return {name: value for name, value in fields.items() if value is not None}


def summarize_record(content: Mapping[str, object]) -> str | None:
    """Hypothesis statement, action title or description, outcome interpretation, or a bundle."""

    shape = record_shape(content)
    if shape == RecordShape.HYPOTHESIS:
        return first_line(str(text_of(content.get("statement"))))
    if shape == RecordShape.ACTION:
        detail = _action_title(content)
        return None if detail is None else first_line(detail)
    if shape in CONTAINER_SHAPES:
        return container_line(content, shape)
    if shape == RecordShape.OUTCOME:
        return first_line(str(text_of(content.get("interpretation"))))
    summary = text_of(content.get("summary"))
    return None if summary is None else first_line(summary)


def memory_body(content: Mapping[str, object]) -> tuple[str, str] | None:
    """A new memory's short text and the words it is found by; None when there is nothing to say.

    The text is the readable line plus the fields that matter for its kind. The words come from
    the values only, so field names shared by every memory never make two memories look alike.
    """

    line = summarize_record(content)
    if line is None or is_container(content):
        return None
    fields = key_fields(content)
    extra = {name: value for name, value in fields.items() if first_line(value) != line}
    parts = [line, *(f"{name}: {first_line(value)}" for name, value in extra.items())]
    return " | ".join(parts), " ".join([line, *(first_line(value) for value in extra.values())])
