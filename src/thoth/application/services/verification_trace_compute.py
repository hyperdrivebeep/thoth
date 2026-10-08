"""Deterministic trace verdict computation and shared trace value readers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast

from pydantic import BaseModel

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.verification_trace import (
    SYSTEM_ACTOR,
    TRACE_SCHEMA_VERSION,
    AppliedConditions,
    CauseOfChange,
    ChangedDependency,
    Comparator,
    CriterionRule,
    CriterionVerdictState,
    ExcludedResult,
    RequirementVerdictState,
    ResultCandidate,
    ResultRecord,
    ResultSelection,
    SelectionPolicy,
    SelectionPolicyName,
    SubjectKind,
    TraceKind,
    TraceRelation,
    TraceSet,
    VerdictActor,
    VerdictBasis,
    VerdictDraft,
    VerdictReasons,
    VerdictRevision,
    seal_verdict,
)

# Keep the original module's imports available without duplicating these definitions.
__all__ = [
    "Subject",
    "_Context",
    "_chain",
    "_children",
    "_criterion_revision",
    "_digest",
    "_ids_of",
    "_judge",
    "_meets",
    "_requirement_revision",
    "_requirement_state",
    "_requirements_of",
    "_select",
    "_show",
    "_value_to_compare",
    "compute_verdicts",
]


Subject = tuple[SubjectKind, str]


@dataclass(frozen=True)
class _Context:
    """What is the same for every verdict in one computation."""

    trigger: str
    changes: tuple[ChangedDependency, ...]
    actor: VerdictActor


# --- reading the set ---


def _ids_of(trace_set: TraceSet, kind: TraceKind) -> tuple[str, ...]:
    return tuple(sorted(item.item_id for item in trace_set.items if item.kind is kind))


def _children(trace_set: TraceSet, requirement_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            link.from_id
            for link in trace_set.links
            if link.relation is TraceRelation.REFINES and link.to_id == requirement_id
        )
    )


def _requirements_of(trace_set: TraceSet, criterion_ids: Iterable[str]) -> tuple[str, ...]:
    wanted = set(criterion_ids)
    return tuple(
        sorted(
            {
                link.to_id
                for link in trace_set.links
                if link.relation is TraceRelation.REFINES and link.from_id in wanted
            }
        )
    )


def _show(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, BaseModel):
        return canonical_payload(value).decode("utf-8")
    if isinstance(value, tuple):
        return "[" + ", ".join(_show(item) for item in cast(tuple[object, ...], value)) + "]"
    return str(value)


def _digest(domain: str, payload: dict[str, object]) -> str:
    return domain_digest(domain, TRACE_SCHEMA_VERSION, canonical_payload(payload))


# --- one criterion ---


def _select(
    rule: CriterionRule, results: list[ResultRecord], policy: SelectionPolicy
) -> ResultSelection:
    def ref(result: ResultRecord) -> ResultCandidate:
        return ResultCandidate(result_id=result.result_id, result_revision=result.result_revision)

    same = sorted(
        (item for item in results if item.condition == rule.condition),
        key=lambda item: (item.observed_at, item.result_revision, item.result_id),
    )
    other = [item for item in results if item.condition != rule.condition]
    if policy.name is SelectionPolicyName.LATEST_PER_CONDITION:
        chosen, older = same[-1:], same[:-1]
    else:
        chosen, older = same, []
    excluded = (
        *(
            ExcludedResult(
                result_id=item.result_id,
                result_revision=item.result_revision,
                reason="SUPERSEDED_BY_LATER_RESULT",
            )
            for item in older
        ),
        *(
            ExcludedResult(
                result_id=item.result_id,
                result_revision=item.result_revision,
                reason="CONDITION_MISMATCH",
            )
            for item in other
        ),
    )
    return ResultSelection(
        policy=policy,
        candidates=tuple(ref(item) for item in sorted(results, key=lambda item: item.result_id)),
        chosen=tuple(ref(item) for item in chosen),
        excluded=excluded,
    )


def _value_to_compare(rule: CriterionRule, result: ResultRecord) -> tuple[Decimal | None, str]:
    """The rounded value to compare, or (None, why there is none)."""
    name = result.result_id
    if result.unit != rule.unit:
        return None, f"UNIT_MISMATCH:{name}:{result.unit or 'none'}!={rule.unit}"
    try:
        counted: Decimal | None = None
        if result.numerator is not None and result.denominator is not None:
            if result.denominator <= 0:
                return None, f"DENOMINATOR_NOT_POSITIVE:{name}"
            counted = rule.rounding.apply(Decimal(result.numerator) / Decimal(result.denominator))
        if result.value is None:
            if result.raw_value is not None and result.raw_value.strip():
                return None, f"VALUE_NOT_A_NUMBER:{name}"
            if counted is None:
                return None, f"VALUE_MISSING:{name}"
            return counted, ""
        rounded = rule.rounding.apply(result.value)
    except ValueError:
        return None, f"VALUE_OUT_OF_RANGE:{name}"
    if counted is not None and counted != rounded:
        return None, f"VALUE_DISAGREES_WITH_COUNTS:{name}"
    return rounded, ""


def _meets(rule: CriterionRule, value: Decimal) -> bool:
    if rule.comparator is Comparator.AT_LEAST:
        return value >= rule.threshold
    return value <= rule.threshold


def _judge(
    rule: CriterionRule, chosen: list[ResultRecord]
) -> tuple[CriterionVerdictState, tuple[str, ...]]:
    if not chosen:
        return CriterionVerdictState.HOLD_NO_RESULT, (f"NO_RESULT:{rule.condition}",)
    failed: list[str] = []
    invalid: list[str] = []
    for result in chosen:
        value, problem = _value_to_compare(rule, result)
        if value is None:
            invalid.append(problem)
        elif not _meets(rule, value):
            need = f"{rule.comparator.value} {format(rule.threshold, 'f')} {rule.unit}"
            failed.append(
                f"THRESHOLD_NOT_MET:{result.result_id}:value={format(value, 'f')} need {need}"
            )
    if failed:
        return CriterionVerdictState.FAIL_COMPUTED, (*failed, *invalid)
    if invalid:
        return CriterionVerdictState.HOLD_INVALID_RESULT, tuple(invalid)
    return CriterionVerdictState.PASS_COMPUTED, ()


def _chain(draft: VerdictDraft, previous: VerdictRevision | None) -> VerdictRevision:
    """Make the revision; if the verdict did not change keep the previous one as it was."""
    candidate = seal_verdict(draft, None if previous is None else previous.revision_digest)
    if previous is not None and candidate.verdict_digest == previous.verdict_digest:
        return previous
    return candidate


def _criterion_revision(
    trace_set: TraceSet,
    criterion_id: str,
    policy: SelectionPolicy,
    context: _Context,
    previous: VerdictRevision | None,
) -> VerdictRevision:
    rule = next((item for item in trace_set.rules if item.criterion_id == criterion_id), None)
    results = sorted(
        (item for item in trace_set.results if item.criterion_id == criterion_id),
        key=lambda item: item.result_id,
    )
    cause_changes = tuple(item for item in context.changes if criterion_id in item.criterion_ids)
    cause = CauseOfChange(trigger=context.trigger, changed=cause_changes)
    inputs = _digest(
        "TRACE_CRITERION_INPUT",
        {"criterion_id": criterion_id, "rule": rule, "results": tuple(results), "policy": policy},
    )
    if rule is None:
        return _chain(
            VerdictDraft(
                subject_kind=SubjectKind.CRITERION,
                subject_id=criterion_id,
                state=CriterionVerdictState.HOLD_NO_RULE,
                cause=cause,
                basis=VerdictBasis(input_digest=inputs),
                conditions=AppliedConditions(),
                actor=context.actor,
                reasons=VerdictReasons(computed=("NO_RULE",)),
            ),
            previous,
        )
    selection = _select(rule, results, policy)
    chosen_ids = {item.result_id for item in selection.chosen}
    chosen = [item for item in results if item.result_id in chosen_ids]
    state, reasons = _judge(rule, chosen)
    refs = tuple(sorted({ref for item in chosen for ref in item.source_span_refs}))
    return _chain(
        VerdictDraft(
            subject_kind=SubjectKind.CRITERION,
            subject_id=criterion_id,
            state=state,
            cause=cause,
            basis=VerdictBasis(input_digest=inputs, source_span_refs=refs),
            selection=selection,
            conditions=AppliedConditions(
                required=rule.required,
                rule_id=rule.rule_id,
                rule_revision=rule.rule_revision,
                condition=rule.condition,
                unit=rule.unit,
                comparator=rule.comparator,
                threshold=rule.threshold,
                rounding=rule.rounding,
            ),
            actor=context.actor,
            reasons=VerdictReasons(computed=reasons),
        ),
        previous,
    )


# --- one requirement ---


def _requirement_state(
    states: list[CriterionVerdictState],
) -> RequirementVerdictState:
    """Never PASS without a required criterion; conditions are never averaged."""
    if not states:
        return RequirementVerdictState.HOLD_NO_CRITERIA
    failed = any(item is CriterionVerdictState.FAIL_COMPUTED for item in states)
    held = any(
        item not in (CriterionVerdictState.PASS_COMPUTED, CriterionVerdictState.FAIL_COMPUTED)
        for item in states
    )
    if failed:
        return (
            RequirementVerdictState.FAIL_WITH_INCOMPLETE_COVERAGE
            if held
            else RequirementVerdictState.FAIL
        )
    return RequirementVerdictState.HOLD_INCOMPLETE if held else RequirementVerdictState.PASS


def _requirement_revision(
    trace_set: TraceSet,
    requirement_id: str,
    criteria: dict[str, VerdictRevision],
    context: _Context,
    previous: VerdictRevision | None,
) -> VerdictRevision:
    children = _children(trace_set, requirement_id)
    rules = {item.criterion_id: item for item in trace_set.rules}
    required = tuple(child for child in children if child not in rules or rules[child].required)
    states = [criteria[child].state for child in required]
    typed = [item for item in states if isinstance(item, CriterionVerdictState)]
    digests = tuple(criteria[child].verdict_digest for child in required)
    refs = tuple(
        sorted({ref for child in required for ref in criteria[child].basis.source_span_refs})
    )
    own = set(children)
    cause_changes = tuple(
        item
        for item in context.changes
        if requirement_id in item.requirement_ids or own & set(item.criterion_ids)
    )
    reasons = (
        tuple(
            f"{child}:{criteria[child].state.value}"
            for child in required
            if criteria[child].state is not CriterionVerdictState.PASS_COMPUTED
        )
        if required
        else ("NO_REQUIRED_CRITERIA",)
    )
    return _chain(
        VerdictDraft(
            subject_kind=SubjectKind.REQUIREMENT,
            subject_id=requirement_id,
            state=_requirement_state(typed),
            cause=CauseOfChange(trigger=context.trigger, changed=cause_changes),
            basis=VerdictBasis(
                input_digest=_digest(
                    "TRACE_REQUIREMENT_INPUT",
                    {"requirement_id": requirement_id, "required": required, "children": digests},
                ),
                source_span_refs=refs,
                child_verdict_digests=digests,
            ),
            conditions=AppliedConditions(required_criteria=required),
            actor=context.actor,
            reasons=VerdictReasons(computed=reasons),
        ),
        previous,
    )


# --- public pure functions ---


def compute_verdicts(
    trace_set: TraceSet,
    policy: SelectionPolicy,
    *,
    computed_at: datetime,
    trigger: str = "RECOMPUTE",
    changes: tuple[ChangedDependency, ...] = (),
    previous: Iterable[VerdictRevision] = (),
    actor_id: str = SYSTEM_ACTOR,
) -> tuple[VerdictRevision, ...]:
    """The current verdict of every criterion and requirement; model-free and repeatable.

    A verdict whose inputs did not change comes back as the very same revision. A changed one is a
    new revision whose parent is the previous revision of that subject.
    """
    last: dict[Subject, VerdictRevision] = {
        (item.subject_kind, item.subject_id): item for item in previous
    }
    context = _Context(
        trigger=trigger,
        changes=changes,
        actor=VerdictActor(actor_id=actor_id, computed_at=computed_at),
    )
    criteria = {
        criterion_id: _criterion_revision(
            trace_set,
            criterion_id,
            policy,
            context,
            last.get((SubjectKind.CRITERION, criterion_id)),
        )
        for criterion_id in _ids_of(trace_set, TraceKind.CRITERION)
    }
    requirements = tuple(
        _requirement_revision(
            trace_set,
            requirement_id,
            criteria,
            context,
            last.get((SubjectKind.REQUIREMENT, requirement_id)),
        )
        for requirement_id in _ids_of(trace_set, TraceKind.REQUIREMENT)
    )
    return (*criteria.values(), *requirements)
