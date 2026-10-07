"""The lesson references of a project, written when a rule or a person records something.

A lesson is added in the same commit as the record it rests on (or, for a confirmed effect, right
after the import that brought the new results) and is never removed. Reading recalls only the
lessons of the row's exact context and says which of them went stale or were refuted. No model is
called and no lesson is a sentence: what is shown is read back from the records they point at.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.hypothesis_same import read_same
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.verification_trace import REVISION_PREFIX
from thoth.domain.discrimination import (
    DiscriminationLedgerRecord,
    DiscriminationResult,
    elimination,
    latest_results,
)
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.hypothesis_same import groups as same_groups
from thoth.domain.lesson_ref import (
    LessonContext,
    LessonKind,
    LessonRef,
    LessonReferenceRecord,
    LessonSource,
    fingerprint_of,
    lesson_id_of,
    recall_lessons,
)
from thoth.domain.trace_closure import ClosureEvent, TraceClosureRecord
from thoth.domain.verdict_link import CurrentVerdict
from thoth.domain.verification_trace import (
    SubjectKind,
    VerdictRevision,
    VerificationVerdictRecord,
)
from thoth.ports.ledger import LedgerPort

KEY = "lesson-references"
DISCRIMINATION_KEY = "hypothesis-discrimination"
CLOSURE_KEY = "trace-closures"
RULE_ACTOR = "system:lesson-rule"
HUMAN_CLOSURES = frozenset({"HUMAN_CLOSED", "WAIVER_RECORDED", "CONDITION_CHANGED"})


def _dump(item: DiscriminationResult | ClosureEvent) -> dict[str, object]:
    """What the record says, without when it was stamped (the stamp is not its content)."""
    content = cast(dict[str, object], item.model_dump(mode="json"))
    content.pop("created_at", None)
    return content


@dataclass(frozen=True)
class _ResultIndex:
    """The test results grouped once: by id, and by hypothesis (each in the order recorded)."""

    by_id: Mapping[str, DiscriminationResult]
    by_hypothesis: Mapping[str, tuple[DiscriminationResult, ...]]

    @staticmethod
    def of(results: tuple[DiscriminationResult, ...]) -> _ResultIndex:
        grouped: dict[str, list[DiscriminationResult]] = {}
        for item in results:
            grouped.setdefault(item.hypothesis_id, []).append(item)
        return _ResultIndex(
            {item.event_id: item for item in results},
            {key: tuple(value) for key, value in grouped.items()},
        )

    def of_hypothesis(self, hypothesis_id: str) -> tuple[DiscriminationResult, ...]:
        return self.by_hypothesis.get(hypothesis_id, ())


class LessonLedger:
    def __init__(self, records: RequestRecords) -> None:
        self._records = records
        self._reader = HypothesisLinkReader(records.ledger)

    @property
    def _ledger(self) -> LedgerPort:
        return self._records.ledger

    def read(self, project_id: str) -> LessonReferenceRecord:
        content = self._reader.content(project_id, EntityType.THREAD, KEY)
        if content is None:
            return LessonReferenceRecord(project_id=project_id)
        return LessonReferenceRecord.model_validate(content)

    def _results(self, project_id: str) -> tuple[DiscriminationResult, ...]:
        content = self._reader.content(project_id, EntityType.THREAD, DISCRIMINATION_KEY)
        if content is None:
            return ()
        return DiscriminationLedgerRecord.model_validate(content).results

    def _closure_events(self, project_id: str) -> tuple[ClosureEvent, ...]:
        content = self._reader.content(project_id, EntityType.THREAD, CLOSURE_KEY)
        return () if content is None else TraceClosureRecord.model_validate(content).events

    def _revision(
        self, project_id: str, digest: str, heads: Mapping[str, str] | None = None
    ) -> VerdictRevision | None:
        content = self._reader.content(
            project_id, EntityType.THREAD, REVISION_PREFIX + digest, heads
        )
        return (
            None if content is None else VerificationVerdictRecord.model_validate(content).revision
        )

    @staticmethod
    def _context(subject_kind: str, subject_id: str, revision: VerdictRevision) -> LessonContext:
        applied = revision.conditions
        return LessonContext(
            subject_kind=subject_kind,  # type: ignore[arg-type]
            subject_id=subject_id,
            unit=applied.unit,
            condition=applied.condition,
            rule_id=applied.rule_id,
            rule_revision=applied.rule_revision,
        )

    def row_now(
        self,
        project_id: str,
        kind: str,
        subject_id: str,
        shown: Mapping[tuple[SubjectKind, str], CurrentVerdict] | None = None,
        heads: Mapping[str, str] | None = None,
    ) -> tuple[LessonContext, str] | None:
        """The context a trace row's verdict stands in now, and that verdict's content digest.

        `shown` is the current verdicts when the caller already read them (a list reads them once).
        """
        if shown is None:
            shown = self._reader.current_verdicts(project_id, self._reader.trace(project_id)[1])
        current = shown.get((SubjectKind(kind), subject_id))
        revision = (
            None if current is None else self._revision(project_id, current.verdict_revision, heads)
        )
        if current is None or revision is None:
            return None
        return self._context(kind, subject_id, revision), current.verdict_digest

    def _add(self, project_id: str, refs: Sequence[LessonRef], actor_id: str) -> None:
        record = self.read(project_id)
        known = {item.lesson_id for item in record.refs}
        fresh = [item for item in refs if item.lesson_id not in known]
        if fresh:
            updated = record.model_copy(update={"refs": (*record.refs, *fresh)})
            self._records.save(project_id, EntityType.THREAD, KEY, updated, actor_id)

    def _ref(
        self,
        kind: LessonKind,
        outcome: str,
        sources: Sequence[LessonSource],
        fingerprint: str,
        context: LessonContext,
        actor_id: str,
        hypothesis_id: str | None = None,
        test_id: str | None = None,
    ) -> LessonRef:
        return LessonRef(
            lesson_id=lesson_id_of(kind, outcome, sources),
            kind=kind,
            outcome=outcome,
            sources=tuple(sources),
            fingerprint=fingerprint,
            context=context,
            hypothesis_id=hypothesis_id,
            test_id=test_id,
            actor_id=actor_id,
            created_at=self._records.clock.now(),
        )

    def after_result(
        self,
        project_id: str,
        hypothesis: HypothesisRecord,
        earlier: tuple[DiscriminationResult, ...],
        event: DiscriminationResult,
        actor_id: str,
    ) -> None:
        """A recorded test result is a lesson, and so is the elimination it newly amounts to."""
        link = hypothesis.verdict_link
        found = (
            None if link is None else self.row_now(project_id, link.subject_kind, link.subject_id)
        )
        if found is None:
            return  # a result with no trace row behind it has no context to be recalled in
        context, _ = found
        source = LessonSource(record_kind="DISCRIMINATION_RESULT", record_id=event.event_id)
        refs = [
            self._ref(
                "TEST_RESULT",
                event.matched,
                [source],
                fingerprint_of({"event": _dump(event)}),
                context,
                actor_id,
                event.hypothesis_id,
                event.test_id,
            )
        ]
        after = elimination((*earlier, event), event.hypothesis_id)
        if after is not None and after != elimination(earlier, event.hypothesis_id):
            fitting = [
                item
                for item in latest_results((*earlier, event), event.hypothesis_id).values()
                if item.matched == "ALTERNATIVE"
            ]
            refs.append(
                self._ref(
                    "ELIMINATION",
                    after,
                    [
                        LessonSource(record_kind="DISCRIMINATION_RESULT", record_id=i.event_id)
                        for i in fitting
                    ],
                    fingerprint_of({"events": [_dump(item) for item in fitting]}),
                    context,
                    actor_id,
                    event.hypothesis_id,
                )
            )
        self._add(project_id, refs, actor_id)

    def after_closure(self, project_id: str, event: ClosureEvent, actor_id: str) -> None:
        """A closure a person recorded is a lesson; a fix is not, until its effect is confirmed."""
        if event.kind not in HUMAN_CLOSURES:
            return
        found = self.row_now(project_id, event.subject_kind, event.subject_id)
        if found is None:
            return
        context, digest = found
        ref = self._ref(
            "HUMAN_CLOSURE",
            event.kind,
            [LessonSource(record_kind="TRACE_CLOSURE", record_id=event.event_id)],
            fingerprint_of({"event": _dump(event), "verdict_digest": digest}),
            context,
            actor_id,
        )
        self._add(project_id, [ref], actor_id)

    def sync_effects(self, project_id: str, rows: Sequence[Mapping[str, object]]) -> None:
        """A fix whose effect the rules confirmed is a lesson, in the confirming verdict context."""
        with self._ledger.transaction():
            self._sync_effects(project_id, rows)

    def _sync_effects(self, project_id: str, rows: Sequence[Mapping[str, object]]) -> None:
        events = {item.event_id: item for item in self._closure_events(project_id)}
        refs: list[LessonRef] = []
        for row in rows:
            effect = row.get("effect_verdict_revision")
            fixes = [
                item
                for item in cast(list[dict[str, object]], row["events"])
                if item["kind"] == "FIX_APPLIED"
            ]
            if row.get("effect_confirmed") is not True or not isinstance(effect, str) or not fixes:
                continue
            fix = events.get(str(fixes[-1]["event_id"]))
            kind, subject_id = str(row["subject_kind"]), str(row["subject_id"])
            revision = self._revision(project_id, effect)
            now = self.row_now(project_id, kind, subject_id)
            if fix is None or revision is None or now is None:
                continue
            refs.append(
                self._ref(
                    "EFFECT_CONFIRMED",
                    "CONFIRMED",
                    [LessonSource(record_kind="TRACE_CLOSURE", record_id=fix.event_id)],
                    fingerprint_of(
                        {"event": _dump(fix), "effect": effect, "verdict_digest": now[1]}
                    ),
                    self._context(kind, subject_id, revision),
                    RULE_ACTOR,
                )
            )
        self._add(project_id, refs, RULE_ACTOR)

    def _fingerprint_now(
        self,
        project_id: str,
        ref: LessonRef,
        results: tuple[DiscriminationResult, ...],
        events: Mapping[str, ClosureEvent],
        rows: Mapping[tuple[str, str], Mapping[str, object]],
        row_now: Callable[[str, str], tuple[LessonContext, str] | None] | None = None,
        index: _ResultIndex | None = None,
    ) -> str | None:
        """The fingerprint of what the lesson rests on now; None when it cannot be found.

        `index` is the results grouped once by id and by hypothesis (a list builds it once); without
        it the results are searched for each lesson.
        """
        index = index or _ResultIndex.of(results)
        first = ref.sources[0].record_id
        if ref.kind == "TEST_RESULT":
            own = index.by_id.get(first)
            if own is None:
                return None
            newest = latest_results(index.of_hypothesis(own.hypothesis_id), own.hypothesis_id).get(
                own.test_id
            )
            return None if newest is None else fingerprint_of({"event": _dump(newest)})
        if ref.kind == "ELIMINATION":
            hypothesis_id = ref.hypothesis_id or ""
            own_results = index.of_hypothesis(hypothesis_id)
            if elimination(own_results, hypothesis_id) != ref.outcome:
                return None
            fitting = [
                i
                for i in latest_results(own_results, hypothesis_id).values()
                if i.matched == "ALTERNATIVE"
            ]
            return fingerprint_of({"events": [_dump(item) for item in fitting]})
        event = events.get(first)
        row_now = row_now or (lambda kind, subject: self.row_now(project_id, kind, subject))
        now = row_now(ref.context.subject_kind, ref.context.subject_id)
        if event is None or now is None:
            return None
        if ref.kind == "HUMAN_CLOSURE":
            return fingerprint_of({"event": _dump(event), "verdict_digest": now[1]})
        row = rows.get((ref.context.subject_kind, ref.context.subject_id))
        effect = None if row is None else row.get("effect_verdict_revision")
        if row is None or row.get("effect_confirmed") is not True or not isinstance(effect, str):
            return None
        return fingerprint_of({"event": _dump(event), "effect": effect, "verdict_digest": now[1]})

    def recall(
        self, project_id: str, rows: Sequence[Mapping[str, object]]
    ) -> list[dict[str, object]]:
        """Each row's lessons of the same context, oldest first, with their state and sources."""
        refs = self.read(project_id).refs
        if not refs:
            return []
        results = self._results(project_id)
        events = {item.event_id: item for item in self._closure_events(project_id)}
        by_row = {(str(item["subject_kind"]), str(item["subject_id"])): item for item in rows}
        # the trace is read once, and each row's context once, however many lessons rest on it
        shown = self._reader.current_verdicts(
            project_id,
            self._reader.trace(project_id)[1],
            {(SubjectKind(r.context.subject_kind), r.context.subject_id) for r in refs},
        )
        heads = self._ledger.read_heads(project_id)
        known: dict[tuple[str, str], tuple[LessonContext, str] | None] = {}

        def row_now(kind: str, subject: str) -> tuple[LessonContext, str] | None:
            if (kind, subject) not in known:
                known[(kind, subject)] = self.row_now(project_id, kind, subject, shown, heads)
            return known[(kind, subject)]

        index = _ResultIndex.of(results)
        prints = {
            item.lesson_id: self._fingerprint_now(
                project_id, item, results, events, by_row, row_now, index
            )
            for item in refs
        }
        # hypotheses a person marked as the same: each member's whole group
        siblings = {
            member: group
            for group in same_groups(read_same(self._reader, project_id))
            for member in group
        }
        found: list[dict[str, object]] = []
        by_subject: dict[tuple[str, str], list[LessonRef]] = {}
        for ref in refs:  # a lesson is recalled only for its own row, so each row sees only its own
            by_subject.setdefault((ref.context.subject_kind, ref.context.subject_id), []).append(
                ref
            )
        for (kind, subject_id), own in by_subject.items():
            now = row_now(kind, subject_id)
            if now is None:
                continue
            for ref, state in recall_lessons(own, now[0], prints, siblings):
                if (ref.context.subject_kind, ref.context.subject_id) == (kind, subject_id):
                    item = self._item(ref, state, index.by_id, events)
                    others = siblings.get(ref.hypothesis_id or "", frozenset()) - {
                        ref.hypothesis_id
                    }
                    found.append(
                        {**item, "same_hypothesis_ids": sorted(others)} if others else item
                    )
        return found

    @staticmethod
    def _item(
        ref: LessonRef,
        state: str,
        results: Mapping[str, DiscriminationResult],
        events: Mapping[str, ClosureEvent],
    ) -> dict[str, object]:
        detail: dict[str, object] = {}
        first = ref.sources[0].record_id
        if ref.kind in ("TEST_RESULT", "ELIMINATION"):
            seen = [results[item.record_id] for item in ref.sources if item.record_id in results]
            detail = {
                "observations": [
                    {
                        "test_id": i.test_id,
                        "matched": i.matched,
                        "observation": i.observation,
                        "evidence_refs": list(i.evidence_refs),
                    }
                    for i in seen
                ]
            }
        elif first in events:
            closed = events[first]
            detail = {
                "closure_kind": closed.kind,
                "basis_ref": closed.basis_ref,
                "note": closed.note,
                "scope": closed.scope,
                "original_state": closed.verdict_state,
            }
        return {
            "lesson_id": ref.lesson_id,
            "kind": ref.kind,
            "outcome": ref.outcome,
            "state": state,
            "subject_kind": ref.context.subject_kind,
            "subject_id": ref.context.subject_id,
            "hypothesis_id": ref.hypothesis_id,
            "test_id": ref.test_id,
            "actor_id": ref.actor_id,
            "created_at": ref.created_at.isoformat(),
            "detail": detail,
        }
