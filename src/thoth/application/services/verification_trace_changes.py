"""Pure trace dependency comparisons, currentness and record replacement."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from thoth.application.services.verification_trace_compute import (
    Subject,
    _children,
    _ids_of,
    _requirements_of,
    _show,
)
from thoth.domain.verification_trace import (
    ChangedDependency,
    CriterionRule,
    Currentness,
    CurrentnessState,
    ResultRecord,
    SelectionPolicy,
    SubjectKind,
    TraceKind,
    TraceRelation,
    TraceSet,
    VerificationTraceRecord,
)

# Keep the original module's imports available without duplicating these definitions.
__all__ = [
    "DependencyKind",
    "_field_changes",
    "_replaced",
    "_result_summary",
    "_rule_summary",
    "dependency_changes",
    "mark_stale",
]


DependencyKind = Literal["RULE", "RESULT", "LINK", "POLICY"]


def _rule_summary(rule: CriterionRule) -> str:
    return f"{rule.comparator.value} {format(rule.threshold, 'f')} {rule.unit} @{rule.condition}"


def _result_summary(result: ResultRecord) -> str:
    value = "none" if result.value is None else format(result.value, "f")
    return f"{value} {result.unit} @{result.condition}"


def _field_changes(
    kind: DependencyKind,
    ref_id: str,
    old: BaseModel,
    new: BaseModel,
    revisions: tuple[int, int],
    scope: tuple[tuple[str, ...], tuple[str, ...]],
) -> list[ChangedDependency]:
    before, after = old.model_dump(mode="python"), new.model_dump(mode="python")
    return [
        ChangedDependency(
            kind=kind,
            ref_id=ref_id,
            field=name,
            before=_show(before[name]),
            after=_show(after[name]),
            before_revision=revisions[0],
            after_revision=revisions[1],
            criterion_ids=scope[0],
            requirement_ids=scope[1],
        )
        for name in sorted(before)
        if before[name] != after[name]
    ]


def dependency_changes(old: TraceSet | None, new: TraceSet) -> tuple[ChangedDependency, ...]:
    """What a verdict depends on that differs between two sets.

    Rules, results and refine-links count. A criterion's title or any other display field does
    not, so editing it never makes a verdict stale.
    """
    before = old or TraceSet()
    changes: list[ChangedDependency] = []

    def scope(criterion_ids: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        extra = (*_requirements_of(before, criterion_ids), *_requirements_of(new, criterion_ids))
        return criterion_ids, tuple(sorted(set(extra)))

    old_rules = {item.rule_id: item for item in before.rules}
    new_rules = {item.rule_id: item for item in new.rules}
    for rule_id in sorted(old_rules.keys() | new_rules.keys()):
        was, now = old_rules.get(rule_id), new_rules.get(rule_id)
        if was is not None and now is not None:
            ids = tuple(sorted({was.criterion_id, now.criterion_id}))
            changes += _field_changes(
                "RULE", rule_id, was, now, (was.rule_revision, now.rule_revision), scope(ids)
            )
            continue
        item = now or was
        assert item is not None
        changes.append(
            ChangedDependency(
                kind="RULE",
                ref_id=rule_id,
                field="*",
                before=None if was is None else _rule_summary(was),
                after=None if now is None else _rule_summary(now),
                before_revision=None if was is None else was.rule_revision,
                after_revision=None if now is None else now.rule_revision,
                criterion_ids=scope((item.criterion_id,))[0],
                requirement_ids=scope((item.criterion_id,))[1],
            )
        )
    old_results = {item.result_id: item for item in before.results}
    new_results = {item.result_id: item for item in new.results}
    for result_id in sorted(old_results.keys() | new_results.keys()):
        was_result, now_result = old_results.get(result_id), new_results.get(result_id)
        if was_result is not None and now_result is not None:
            ids = tuple(sorted({was_result.criterion_id, now_result.criterion_id}))
            changes += _field_changes(
                "RESULT",
                result_id,
                was_result,
                now_result,
                (was_result.result_revision, now_result.result_revision),
                scope(ids),
            )
            continue
        present = now_result or was_result
        assert present is not None
        changes.append(
            ChangedDependency(
                kind="RESULT",
                ref_id=result_id,
                field="*",
                before=None if was_result is None else _result_summary(was_result),
                after=None if now_result is None else _result_summary(now_result),
                before_revision=None if was_result is None else was_result.result_revision,
                after_revision=None if now_result is None else now_result.result_revision,
                criterion_ids=scope((present.criterion_id,))[0],
                requirement_ids=scope((present.criterion_id,))[1],
            )
        )
    pairs_before = {
        (link.from_id, link.to_id)
        for link in before.links
        if link.relation is TraceRelation.REFINES
    }
    pairs_after = {
        (link.from_id, link.to_id) for link in new.links if link.relation is TraceRelation.REFINES
    }
    for source, target in sorted(pairs_before ^ pairs_after):
        added = (source, target) in pairs_after
        changes.append(
            ChangedDependency(
                kind="LINK",
                ref_id=f"{source}->{target}",
                field="*",
                before=None if added else "linked",
                after="linked" if added else None,
                criterion_ids=(source,),
                requirement_ids=(target,),
            )
        )
    return tuple(changes)


def mark_stale(
    trace_set: TraceSet, changes: Iterable[ChangedDependency]
) -> dict[Subject, Currentness]:
    """Currentness of every criterion and requirement; only those the changes reach are stale."""
    pending = tuple(changes)

    def state(hits: tuple[ChangedDependency, ...]) -> Currentness:
        if not hits:
            return Currentness()
        return Currentness(state=CurrentnessState.STALE_BASIS, changed_dependencies=hits)

    result: dict[Subject, Currentness] = {}
    for criterion_id in _ids_of(trace_set, TraceKind.CRITERION):
        hits = tuple(item for item in pending if criterion_id in item.criterion_ids)
        result[(SubjectKind.CRITERION, criterion_id)] = state(hits)
    for requirement_id in _ids_of(trace_set, TraceKind.REQUIREMENT):
        own = set(_children(trace_set, requirement_id))
        hits = tuple(
            item
            for item in pending
            if requirement_id in item.requirement_ids or own & set(item.criterion_ids)
        )
        result[(SubjectKind.REQUIREMENT, requirement_id)] = state(hits)
    return result


def _replaced(
    project_id: str,
    current: VerificationTraceRecord | None,
    trace_set: TraceSet,
    policy: SelectionPolicy | None,
) -> VerificationTraceRecord:
    """The record with a new set in it; what the new set changed is remembered as pending."""
    if current is None:
        return VerificationTraceRecord(
            project_id=project_id, trace_set=trace_set, policy=policy or SelectionPolicy()
        )
    wanted = policy or current.policy
    found = list(dependency_changes(current.trace_set, trace_set))
    if wanted != current.policy:
        found.append(
            ChangedDependency(
                kind="POLICY",
                ref_id=wanted.name.value,
                field="policy",
                before=_show(current.policy),
                after=_show(wanted),
                before_revision=current.policy.version,
                after_revision=wanted.version,
                criterion_ids=_ids_of(trace_set, TraceKind.CRITERION),
                requirement_ids=_ids_of(trace_set, TraceKind.REQUIREMENT),
            )
        )
    merged = tuple(dict.fromkeys((*current.pending_changes, *found)))
    return current.model_copy(
        update={"trace_set": trace_set, "policy": wanted, "pending_changes": merged}
    )
