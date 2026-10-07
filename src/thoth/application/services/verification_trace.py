"""Verdicts computed by rule from a trace set, and where a project keeps them.

Nothing here calls a model. The same trace set, policy and previous verdicts always give the same
verdicts: compute_verdicts is a pure function, and a verdict whose inputs did not change is kept
as it was (same revision, same confirmations). A new revision is made only when its inputs
changed, and it links to the revision before it. Recomputing is always an explicit user action.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from pydantic import BaseModel

from thoth.application.services.request_records import RequestRecords
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.verification_trace import (
    SYSTEM_ACTOR,
    TRACE_SCHEMA_VERSION,
    AppliedConditions,
    CauseOfChange,
    ChangedDependency,
    Comparator,
    Confirmation,
    CriterionRule,
    CriterionVerdictState,
    Currentness,
    CurrentnessState,
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
    VerificationTraceRecord,
    VerificationVerdictRecord,
    seal_verdict,
    subject_key,
)

KEY = "verification-trace:project"
REVISION_PREFIX = "trace-verdict:"
Subject = tuple[SubjectKind, str]
_Built = tuple[VerificationTraceRecord, tuple[VerdictRevision, ...]]
DependencyKind = Literal["RULE", "RESULT", "LINK", "POLICY"]


class TraceError(ValueError):
    pass


class TraceConflict(TraceError):
    pass


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


# --- storage ---


@dataclass(frozen=True)
class HistoryPage:
    """Revisions of one verdict, newest first, older than the cursor that was asked for."""

    revisions: tuple[VerdictRevision, ...]
    total: int
    next_before: str | None  # pass it back to read the revisions before this page


class VerificationTraceService:
    """One trace record per project, changed only with the digest the caller read.

    Each verdict revision is its own record, written once in the same ledger commit as the trace
    record that lists it, so a save writes the trace and the new revisions and nothing else.
    """

    def __init__(self, records: RequestRecords) -> None:
        self._records = records

    def read(self, project_id: str) -> tuple[str | None, VerificationTraceRecord | None]:
        record = self._records.read(project_id, EntityType.THREAD, KEY)
        if record is None:
            return None, None
        return record[0].revision_digest, VerificationTraceRecord.model_validate(record[1])

    def revision(self, project_id: str, digest: str) -> VerdictRevision:
        stored = self._records.read(project_id, EntityType.THREAD, REVISION_PREFIX + digest)
        if stored is None:
            raise TraceError("TRACE_VERDICT_REVISION_MISSING")
        return VerificationVerdictRecord.model_validate(stored[1]).revision

    def current_verdicts(
        self, project_id: str, record: VerificationTraceRecord
    ) -> dict[Subject, VerdictRevision]:
        return {
            key: self.revision(project_id, digest)
            for key, digest in record.current_digests().items()
        }

    def history(
        self,
        project_id: str,
        record: VerificationTraceRecord,
        kind: SubjectKind,
        subject_id: str,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> HistoryPage:
        digests = record.history.get(subject_key(kind, subject_id), ())
        older = digests
        if before is not None:
            if before not in digests:
                raise TraceError("TRACE_HISTORY_CURSOR_UNKNOWN")
            older = digests[: digests.index(before)]
        page = older[max(len(older) - limit, 0) :]
        return HistoryPage(
            revisions=tuple(self.revision(project_id, digest) for digest in reversed(page)),
            total=len(digests),
            next_before=page[0] if page and len(older) > len(page) else None,
        )

    def currentness(self, project_id: str) -> dict[Subject, Currentness]:
        _, record = self.read(project_id)
        if record is None:
            return {}
        return mark_stale(record.trace_set, record.pending_changes)

    def mark_stale(
        self,
        project_id: str,
        changes: tuple[ChangedDependency, ...],
        expected_digest: str | None,
        actor_id: str,
    ) -> str:
        """Remember changes that reach existing verdicts; they stay as they are until recomputed."""

        def build(current: VerificationTraceRecord | None) -> _Built:
            if current is None:
                raise TraceError("TRACE_NOT_FOUND")
            merged = tuple(dict.fromkeys((*current.pending_changes, *changes)))
            return current.model_copy(update={"pending_changes": merged}), ()

        return self._write(project_id, expected_digest, build, actor_id)

    def replace_trace_set(
        self,
        project_id: str,
        trace_set: TraceSet,
        expected_digest: str | None,
        actor_id: str,
        policy: SelectionPolicy | None = None,
    ) -> str:
        """Store a new set. What it changed is remembered as stale; no verdict is recomputed."""
        return self._write(
            project_id,
            expected_digest,
            lambda current: (_replaced(project_id, current, trace_set, policy), ()),
            actor_id,
        )

    def recompute(
        self,
        project_id: str,
        expected_digest: str | None,
        actor_id: str,
        trigger: str = "RECOMPUTE",
    ) -> str:
        """The user asked for new verdicts. Unchanged ones keep their revision."""

        def build(current: VerificationTraceRecord | None) -> _Built:
            if current is None:
                raise TraceError("TRACE_NOT_FOUND")
            return self._recomputed(project_id, current, trigger)

        return self._write(project_id, expected_digest, build, actor_id)

    def apply_import(
        self,
        project_id: str,
        trace_set: TraceSet,
        expected_digest: str | None,
        actor_id: str,
        trigger: str = "CSV_IMPORT",
    ) -> str:
        """Store the imported set and compute the verdicts it changes in one write."""
        return self._write(
            project_id,
            expected_digest,
            lambda current: self._recomputed(
                project_id, _replaced(project_id, current, trace_set, None), trigger
            ),
            actor_id,
        )

    def _recomputed(
        self, project_id: str, current: VerificationTraceRecord, trigger: str
    ) -> _Built:
        previous = tuple(self.current_verdicts(project_id, current).values())
        verdicts = compute_verdicts(
            current.trace_set,
            current.policy,
            computed_at=self._records.clock.now(),
            trigger="INITIAL_COMPUTE" if not previous and trigger == "RECOMPUTE" else trigger,
            changes=current.pending_changes,
            previous=previous,
        )
        known = {item.revision_digest for item in previous}
        added = tuple(item for item in verdicts if item.revision_digest not in known)
        history = dict(current.history)
        for item in added:
            key = subject_key(item.subject_kind, item.subject_id)
            history[key] = (*history.get(key, ()), item.revision_digest)
        return current.model_copy(update={"history": history, "pending_changes": ()}), added

    def confirm(
        self,
        project_id: str,
        verdict_revision_digest: str,
        actor_id: str,
        rationale: str,
        expected_digest: str | None,
    ) -> str:
        """Record that a person looked at this revision. The verdict itself is not touched."""

        def build(current: VerificationTraceRecord | None) -> _Built:
            if current is None:
                raise TraceError("TRACE_NOT_FOUND")
            if verdict_revision_digest not in current.current_digests().values():
                raise TraceError("TRACE_CONFIRM_TARGET_NOT_CURRENT")
            confirmation = Confirmation(
                verdict_revision_digest=verdict_revision_digest,
                actor_id=actor_id,
                confirmed_at=self._records.clock.now(),
                rationale=rationale,
            )
            return current.model_copy(
                update={"confirmations": (*current.confirmations, confirmation)}
            ), ()

        return self._write(project_id, expected_digest, build, actor_id)

    def _write(
        self,
        project_id: str,
        expected_digest: str | None,
        build: Callable[[VerificationTraceRecord | None], _Built],
        actor_id: str,
    ) -> str:
        """The trace record and every new revision are committed together or not at all."""
        with self._records.ledger.transaction():
            digest, current = self.read(project_id)
            if digest != expected_digest:
                raise TraceConflict("TRACE_REVISION_CONFLICT")
            updated, revisions = build(current)
            main_ref, main = self._records.stage(
                project_id, EntityType.THREAD, KEY, updated, actor_id
            )
            staged = [main]
            for revision in revisions:
                _, extra = self._records.stage(
                    project_id,
                    EntityType.THREAD,
                    REVISION_PREFIX + revision.revision_digest,
                    VerificationVerdictRecord(project_id=project_id, revision=revision),
                    actor_id,
                )
                staged.append(extra)
            self._records.commit_staged(tuple(staged))
            return main_ref.revision_digest
