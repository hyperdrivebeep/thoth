"""The exact content of a protected action, and what changed in it since it was approved.

Approval binds what is actually sent (ten items, plus any field this code does not recognise).
How the step is shown (the `presentation` object, order, colour, folding) never changes the digest.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from itertools import zip_longest
from typing import Literal, cast

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest

# Only these are display-only. A free-form step may use `title`, `summary` or `label` for what it
# actually sends, so those count as material: when it is unclear, the action is approved again.
PRESENTATION_KEYS = frozenset({"presentation", "order", "color", "collapsed"})
DERIVED_KEYS = frozenset({"step_id", "state", "policy_state", "required_processes", "impact"})
_KNOWN_KEYS = frozenset(
    {
        "action_kind",
        "action_family",
        "action_ref",
        "targets",
        "target_digests",
        "scope",
        "content",
        "channel",
        "disclosed_data",
        "required_roles",
        "risk_tier",
        "reversibility",
        "effect_vector",
        "external_commitments",
        "execution_conditions",
        "input_digests",
        "inputs",
        "preconditions",
        "stop_conditions",
        "timeout_seconds",
        "output_contract",
    }
)


class ProtectedAttachment(DomainModel):
    name: str
    digest: str


class ProtectedContent(DomainModel):
    body_text: str = ""
    attachments: tuple[ProtectedAttachment, ...] = ()
    input_digests: tuple[str, ...] = ()


class ExecutionConditions(DomainModel):
    basis_evidence_revision: str | None = None
    cutoff_at: str | None = None
    valid_until: str | None = None
    preconditions: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    output_contract: dict[str, object] = Field(default_factory=dict)


class ProtectedActionPayload(DomainModel):
    action_kind: str
    action_ref: str | None = None
    targets: tuple[str, ...] = ()
    scope: dict[str, str] = Field(default_factory=dict)
    content: ProtectedContent = Field(default_factory=ProtectedContent)
    channel: str = ""
    disclosed_data: tuple[str, ...] = ()
    execution_roles: tuple[str, ...] = ()
    risk_tier: str = ""
    reversibility: str = ""
    effects: dict[str, object] = Field(default_factory=dict)
    external_commitments: tuple[str, ...] = ()
    execution_conditions: ExecutionConditions = Field(default_factory=ExecutionConditions)
    # Anything not recognised is treated as material, so a new field forces a new approval.
    unknown_fields: dict[str, object] = Field(default_factory=dict)
    schema_version: str = "1.0.0"


def _json(value: object) -> object:
    return cast(object, json.loads(json.dumps(value, default=str, sort_keys=True)))


def _texts(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in cast(list[object], list(value)))  # pyright: ignore[reportUnknownArgumentType]


def _items(value: object) -> list[object]:
    return list(cast(list[object], value)) if isinstance(value, list | tuple) else []


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): child for key, child in cast(Mapping[object, object], value).items()}


def payload_from_step(step: Mapping[str, object]) -> ProtectedActionPayload:
    content = _mapping(step.get("content"))
    conditions = _mapping(step.get("execution_conditions"))
    timeout = step.get("timeout_seconds")
    ref = step.get("action_ref")
    return ProtectedActionPayload(
        action_kind=str(step.get("action_kind") or step.get("action_family") or "UNSPECIFIED"),
        action_ref=ref if isinstance(ref, str) else None,
        targets=_texts(step.get("targets") or step.get("target_digests")),
        scope={key: str(child) for key, child in _mapping(step.get("scope")).items()},
        content=ProtectedContent(
            body_text=str(content.get("body_text", "")),
            attachments=tuple(
                ProtectedAttachment(
                    name=str(item.get("name", "")), digest=str(item.get("digest", ""))
                )
                for item in map(_mapping, _items(content.get("attachments")))
            ),
            input_digests=_texts(step.get("input_digests") or step.get("inputs")),
        ),
        channel=str(step.get("channel", "")),
        disclosed_data=_texts(step.get("disclosed_data")),
        execution_roles=_texts(step.get("required_roles")),
        risk_tier=str(step.get("risk_tier", "")),
        reversibility=str(step.get("reversibility", "")),
        effects=cast(dict[str, object], _json(_mapping(step.get("effect_vector")))),
        external_commitments=_texts(step.get("external_commitments")),
        execution_conditions=ExecutionConditions(
            basis_evidence_revision=_optional(conditions.get("basis_evidence_revision")),
            cutoff_at=_optional(conditions.get("cutoff_at")),
            valid_until=_optional(conditions.get("valid_until")),
            preconditions=_texts(step.get("preconditions")),
            stop_conditions=_texts(step.get("stop_conditions")),
            timeout_seconds=timeout if isinstance(timeout, int) else None,
            output_contract=cast(dict[str, object], _json(_mapping(step.get("output_contract")))),
        ),
        unknown_fields=cast(
            dict[str, object],
            _json(
                {
                    key: child
                    for key, child in step.items()
                    if key not in _KNOWN_KEYS | PRESENTATION_KEYS | DERIVED_KEYS
                }
            ),
        ),
    )


def _optional(value: object) -> str | None:
    return None if value is None else str(value)


def payload_digest(payload: ProtectedActionPayload) -> str:
    return domain_digest(
        "PROTECTED_ACTION_PAYLOAD", "1.0.0", canonical_payload(payload.model_dump(mode="json"))
    )


class WordSegment(DomainModel):
    op: Literal["keep", "add", "del"]
    text: str


class AttachmentChange(DomainModel):
    before_name: str | None = None
    before_digest: str | None = None
    after_name: str | None = None
    after_digest: str | None = None


class MaterialChange(DomainModel):
    field: str
    label: str
    before: str
    after: str
    words: tuple[WordSegment, ...] = ()
    attachments: tuple[AttachmentChange, ...] = ()


def word_diff(before: str, after: str) -> tuple[WordSegment, ...]:
    old, new = before.split(), after.split()
    segments: list[WordSegment] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            segments.extend(WordSegment(op="keep", text=word) for word in old[i1:i2])
            continue
        if i2 > i1:
            segments.append(WordSegment(op="del", text=" ".join(old[i1:i2])))
        if j2 > j1:
            segments.append(WordSegment(op="add", text=" ".join(new[j1:j2])))
    return tuple(segments)


_LABELS = {
    "action": "행동 종류",
    "targets": "대상",
    "scope": "범위",
    "body": "본문",
    "attachments": "첨부",
    "inputs": "입력 자료",
    "channel": "전달 경로",
    "disclosed": "공개되는 정보",
    "roles": "실행 권한 역할",
    "risk": "위험 등급",
    "reversibility": "되돌릴 수 있는지",
    "effects": "실제 영향",
    "commitments": "외부에 하는 약속",
    "conditions": "실행 조건",
    "unknown": "알 수 없는 새 항목",
}


def _show(value: object) -> str:
    return (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def material_changes(
    before: ProtectedActionPayload, after: ProtectedActionPayload
) -> tuple[MaterialChange, ...]:
    """Items that differ, in a fixed order, with word-level text and attachment detail."""

    def group(before_value: object, after_value: object, key: str) -> MaterialChange | None:
        if before_value == after_value:
            return None
        return MaterialChange(
            field=key, label=_LABELS[key], before=_show(before_value), after=_show(after_value)
        )

    b, a = before, after
    found: list[MaterialChange | None] = [
        group((b.action_kind, b.action_ref), (a.action_kind, a.action_ref), "action"),
        group(list(b.targets), list(a.targets), "targets"),
        group(b.scope, a.scope, "scope"),
        _body_change(b, a),
        _attachment_change(b, a),
        group(list(b.content.input_digests), list(a.content.input_digests), "inputs"),
        group(b.channel, a.channel, "channel"),
        group(list(b.disclosed_data), list(a.disclosed_data), "disclosed"),
        group(list(b.execution_roles), list(a.execution_roles), "roles"),
        group(b.risk_tier, a.risk_tier, "risk"),
        group(b.reversibility, a.reversibility, "reversibility"),
        group(b.effects, a.effects, "effects"),
        group(list(b.external_commitments), list(a.external_commitments), "commitments"),
        group(
            b.execution_conditions.model_dump(mode="json"),
            a.execution_conditions.model_dump(mode="json"),
            "conditions",
        ),
        group(b.unknown_fields, a.unknown_fields, "unknown"),
    ]
    return tuple(item for item in found if item is not None)


def _body_change(
    before: ProtectedActionPayload, after: ProtectedActionPayload
) -> MaterialChange | None:
    if before.content.body_text == after.content.body_text:
        return None
    return MaterialChange(
        field="body",
        label=_LABELS["body"],
        before=before.content.body_text,
        after=after.content.body_text,
        words=word_diff(before.content.body_text, after.content.body_text),
    )


def _attachment_change(
    before: ProtectedActionPayload, after: ProtectedActionPayload
) -> MaterialChange | None:
    old, new = before.content.attachments, after.content.attachments
    if old == new:
        return None
    removed = [item for item in old if item not in new]
    added = [item for item in new if item not in old]
    pairs = tuple(
        AttachmentChange(
            before_name=None if gone is None else gone.name,
            before_digest=None if gone is None else gone.digest,
            after_name=None if came is None else came.name,
            after_digest=None if came is None else came.digest,
        )
        for gone, came in zip_longest(removed, added)
    )
    return MaterialChange(
        field="attachments",
        label=_LABELS["attachments"],
        before=", ".join(item.name for item in old),
        after=", ".join(item.name for item in new),
        attachments=pairs,
    )
