"""Pure comparisons of already-read results and their recorded reasons."""

from collections.abc import Iterable, Mapping
from typing import Literal, cast

from thoth.application.services.research_criterion_delta import CriterionView, pair_criteria
from thoth.application.services.research_followup_calculations import _basis_currentness
from thoth.application.services.revision_diff import semantic_diff
from thoth.domain.research_followup import (
    DecisionDelta,
    DecisionDeltaGroup,
    ResultIdentity,
)
from thoth.domain.research_reference import RevisionRef
from thoth.domain.revision import SemanticDiffEntry

DeltaKind = Literal["CONTENT", "EVIDENCE", "CONDITION", "STATUS", "ACTION", "OTHER"]


def decision_delta(
    *,
    project_id: str,
    thread_id: str,
    before: ResultIdentity,
    after: ResultIdentity,
    before_manifest: Mapping[str, object] | None,
    after_manifest: Mapping[str, object] | None,
    before_currentness: Mapping[str, object],
    after_currentness: Mapping[str, object],
    before_criteria: tuple[CriterionView, ...] = (),
    after_criteria: tuple[CriterionView, ...] = (),
) -> DecisionDelta:
    if before_manifest is None or after_manifest is None:
        return DecisionDelta(
            project_id=project_id,
            thread_id=thread_id,
            before=before,
            after=after,
            state="UNAVAILABLE",
            reason_state="UNKNOWN_REASON",
            basis_currentness={
                "before": _basis_currentness(before_currentness),
                "after": _basis_currentness(after_currentness),
            },
        )
    before_payload = _delta_payload(before_manifest)
    after_payload = _delta_payload(after_manifest)
    changes = semantic_diff(before_payload, after_payload)
    groups = _delta_groups(changes)
    reason_codes = _reason_codes(after_manifest)
    reason_refs = _reason_refs(after_manifest) if reason_codes else ()
    # Criteria are paired only when both results' criteria were readable; one side alone would make
    # every criterion look added or removed.
    comparable = bool(before_criteria) and bool(after_criteria)
    return DecisionDelta(
        project_id=project_id,
        thread_id=thread_id,
        before=before,
        after=after,
        state="NO_CHANGE" if not groups else "CHANGED",
        groups=groups,
        reason_state="RECORDED" if reason_codes else "UNKNOWN_REASON",
        reason_codes=reason_codes,
        reason_refs=reason_refs,
        basis_currentness={
            "before": _basis_currentness(before_currentness),
            "after": _basis_currentness(after_currentness),
        },
        criteria=pair_criteria(before_criteria, after_criteria) if comparable else (),
        criteria_state="AVAILABLE" if comparable else "UNAVAILABLE",
    )


def _delta_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    result = manifest.get("result")
    payload: dict[str, object] = {
        "phase": manifest.get("phase"),
        "completion": manifest.get("completion"),
        "terminal_reason": manifest.get("terminal_reason"),
        "gaps": manifest.get("gaps"),
        "next_steps": manifest.get("next_steps"),
        "result": result if isinstance(result, Mapping) else {},
    }
    return payload


def _delta_groups(changes: tuple[SemanticDiffEntry, ...]) -> tuple[DecisionDeltaGroup, ...]:
    grouped: dict[DeltaKind, list[SemanticDiffEntry]] = {}
    for change in changes:
        grouped.setdefault(_change_kind(change.path), []).append(change)
    return tuple(
        DecisionDeltaGroup(
            kind=kind,
            trace_paths=tuple(item.path for item in items),
            changes=tuple(items),
        )
        for kind, items in sorted(grouped.items())
    )


def _change_kind(path: str) -> DeltaKind:
    if path.startswith("/result/answer"):
        return "CONTENT"
    if path.startswith("/result/coverage") or "/evidence" in path or "/source" in path:
        return "EVIDENCE"
    if "/hypothes" in path or "/condition" in path:
        return "CONDITION"
    if "/action" in path or "/next_steps" in path:
        return "ACTION"
    if path in {"/phase", "/completion", "/terminal_reason"} or "/status" in path:
        return "STATUS"
    return "OTHER"


def _reason_codes(manifest: Mapping[str, object]) -> tuple[str, ...]:
    result = manifest.get("result")
    empty: Mapping[str, object] = {}
    result_map: Mapping[str, object] = (
        cast(Mapping[str, object], result) if isinstance(result, Mapping) else empty
    )
    coverage_raw = result_map.get("coverage")
    coverage: Mapping[str, object] = (
        cast(Mapping[str, object], coverage_raw) if isinstance(coverage_raw, Mapping) else empty
    )
    values: list[str] = []
    for key in ("terminal_reason", "gaps", "next_steps"):
        raw = manifest.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list | tuple):
            values.extend(
                str(item)
                for item in _object_tuple(cast(list[object] | tuple[object, ...], raw))
                if str(item)
            )
    for key in ("reasons", "allowed_next_steps", "conflicts"):
        raw = coverage.get(key)
        if isinstance(raw, list | tuple):
            values.extend(
                str(item)
                for item in _object_tuple(cast(list[object] | tuple[object, ...], raw))
                if str(item)
            )
    gates_raw = coverage.get("gates")
    empty_gates: Mapping[object, object] = {}
    gates: Mapping[object, object] = (
        cast(Mapping[object, object], gates_raw) if isinstance(gates_raw, Mapping) else empty_gates
    )
    values.extend(str(target) for target, state in gates.items() if state == "HOLD")
    return tuple(item for item in dict.fromkeys(values) if item and item != "None")


def _reason_refs(manifest: Mapping[str, object]) -> tuple[RevisionRef, ...]:
    refs = manifest.get("record_refs")
    if not isinstance(refs, list | tuple):
        return ()
    parsed: list[RevisionRef] = []
    for item in _object_tuple(cast(list[object] | tuple[object, ...], refs)):
        if isinstance(item, Mapping):
            parsed.append(RevisionRef.model_validate(item))
    return tuple(parsed)


def _object_tuple(value: list[object] | tuple[object, ...]) -> tuple[object, ...]:
    return tuple(cast(Iterable[object], value))
