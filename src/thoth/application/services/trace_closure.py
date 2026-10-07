"""A person's closure of a trace row that was not met, and the one effect the rules confirm.

A closure is a decision made outside the system that a person records with the document it rests
on. It is an event added to one project record; the verdict of the row is never touched, so the
rule's own word and its history stay. THOTH grants no waiver: it only writes down that somebody did.
"""

from __future__ import annotations

from thoth.application.services.human_actor import require_human_actor
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.lesson_ledger import LessonLedger
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.verification_trace import REVISION_PREFIX
from thoth.domain.enums import EntityType
from thoth.domain.trace_closure import (
    ClosureEvent,
    ClosureKind,
    TraceClosureRecord,
    effect_confirmed_by,
    is_met,
)
from thoth.domain.verification_trace import (
    SubjectKind,
    VerdictRevision,
    VerificationTraceRecord,
    VerificationVerdictRecord,
    subject_key,
)

KEY = "trace-closures"
CLOSURE_BASIS_REQUIRED = "CLOSURE_BASIS_REQUIRED"
CLOSURE_SCOPE_REQUIRED = "CLOSURE_SCOPE_REQUIRED"
CLOSURE_ROW_NOT_FOUND = "CLOSURE_ROW_NOT_FOUND"
CLOSURE_ROW_ALREADY_MET = "CLOSURE_ROW_ALREADY_MET"
CLOSURE_VERDICT_CHANGED_AGAIN = "CLOSURE_VERDICT_CHANGED_AGAIN"


class ClosureRefused(ValueError):
    """The closure is not recorded; the message is the reason code."""


class TraceClosures:
    def __init__(self, records: RequestRecords) -> None:
        self._records = records
        self._reader = HypothesisLinkReader(records.ledger)
        self._lessons = LessonLedger(records)

    def read(self, project_id: str) -> TraceClosureRecord:
        content = self._reader.content(project_id, EntityType.THREAD, KEY)
        if content is None:
            return TraceClosureRecord(project_id=project_id)
        return TraceClosureRecord.model_validate(content)

    def record(
        self,
        *,
        project_id: str,
        subject_kind: SubjectKind,
        subject_id: str,
        kind: ClosureKind,
        basis_ref: str,
        note: str,
        scope: str | None,
        current_verdict_revision: str,
        actor_id: str,
    ) -> ClosureEvent:
        require_human_actor(actor_id)
        if not basis_ref.strip():
            raise ClosureRefused(CLOSURE_BASIS_REQUIRED)
        if kind == "CONDITION_CHANGED" and not (scope or "").strip():
            raise ClosureRefused(CLOSURE_SCOPE_REQUIRED)
        with self._records.ledger.transaction():
            _, trace = self._reader.trace(project_id)
            shown = self._reader.current_verdicts(project_id, trace)
            current = shown.get((subject_kind, subject_id))
            if current is None:
                raise ClosureRefused(CLOSURE_ROW_NOT_FOUND)
            if current.verdict_revision != current_verdict_revision:
                raise ClosureRefused(CLOSURE_VERDICT_CHANGED_AGAIN)
            if is_met(current.state):
                raise ClosureRefused(CLOSURE_ROW_ALREADY_MET)
            event = ClosureEvent(
                event_id=self._records.ids.new("trace-closure"),
                subject_kind=subject_kind.value,  # type: ignore[arg-type]
                subject_id=subject_id,
                kind=kind,
                basis_ref=basis_ref.strip(),
                note=note.strip(),
                scope=(scope or "").strip() or None if kind == "CONDITION_CHANGED" else None,
                verdict_revision=current.verdict_revision,
                verdict_digest=current.verdict_digest,
                verdict_state=current.state,
                actor_id=actor_id,
                created_at=self._records.clock.now(),
            )
            earlier = self.read(project_id)
            self._records.save(
                project_id,
                EntityType.THREAD,
                KEY,
                earlier.model_copy(update={"events": (*earlier.events, event)}),
                actor_id,
            )
            self._lessons.after_closure(project_id, event, actor_id)
            return event

    def _revision(self, project_id: str, digest: str) -> VerdictRevision | None:
        content = self._reader.content(project_id, EntityType.THREAD, REVISION_PREFIX + digest)
        return (
            None if content is None else VerificationVerdictRecord.model_validate(content).revision
        )

    def _confirmed(
        self, project_id: str, trace: VerificationTraceRecord, fix: ClosureEvent
    ) -> VerdictRevision | None:
        """The later verdict that confirms the fix, from the row's own history after the fix."""
        history = trace.history.get(subject_key(SubjectKind(fix.subject_kind), fix.subject_id), ())
        if fix.verdict_revision not in history:
            return None
        at = history.index(fix.verdict_revision)
        before = self._revision(project_id, history[at])
        later = [self._revision(project_id, digest) for digest in history[at + 1 :]]
        if before is None:
            return None
        return effect_confirmed_by(before, [item for item in later if item is not None])

    def view(self, project_id: str, *, with_current: bool = True) -> list[dict[str, object]]:
        """Each row with a closure: what was recorded, and whether the rules confirm the effect.

        `with_current` reads where each row stands now (its state and whether it moved since the
        record). A reader that needs only the records and the confirmed effect (the CSV note) can
        leave it off and skip reading every row's verdict.
        """
        _, trace = self._reader.trace(project_id)
        if trace is None:
            return []
        rows: dict[tuple[str, str], list[ClosureEvent]] = {}
        for event in self.read(project_id).events:
            rows.setdefault((event.subject_kind, event.subject_id), []).append(event)
        shown = (
            self._reader.current_verdicts(
                project_id, trace, {(SubjectKind(kind), subject) for kind, subject in rows}
            )
            if with_current
            else {}
        )
        found: list[dict[str, object]] = []
        for (kind, subject_id), events in rows.items():
            current = shown.get((SubjectKind(kind), subject_id))
            fixes = [item for item in events if item.kind == "FIX_APPLIED"]
            confirmed = self._confirmed(project_id, trace, fixes[-1]) if fixes else None
            found.append(
                {
                    "subject_kind": kind,
                    "subject_id": subject_id,
                    "current_state": None if current is None else current.state,
                    "effect_confirmed": confirmed is not None,
                    "effect_verdict_revision": None
                    if confirmed is None
                    else confirmed.revision_digest,
                    "verdict_changed_since": current is None
                    or current.verdict_digest != events[-1].verdict_digest,
                    "events": [item.model_dump(mode="json") for item in events],
                }
            )
        return found
