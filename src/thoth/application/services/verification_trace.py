"""Verdicts computed by rule from a trace set, and where a project keeps them.

Nothing here calls a model. The same trace set, policy and previous verdicts always give the same
verdicts: compute_verdicts is a pure function, and a verdict whose inputs did not change is kept
as it was (same revision, same confirmations). A new revision is made only when its inputs
changed, and it links to the revision before it. Recomputing is always an explicit user action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.verification_trace_changes import (
    DependencyKind as DependencyKind,
)
from thoth.application.services.verification_trace_changes import (
    _field_changes as _field_changes,
)
from thoth.application.services.verification_trace_changes import (
    _replaced as _replaced,
)
from thoth.application.services.verification_trace_changes import (
    _result_summary as _result_summary,
)
from thoth.application.services.verification_trace_changes import (
    _rule_summary as _rule_summary,
)
from thoth.application.services.verification_trace_changes import (
    dependency_changes as dependency_changes,
)
from thoth.application.services.verification_trace_changes import (
    mark_stale as mark_stale,
)
from thoth.application.services.verification_trace_compute import (
    Subject as Subject,
)
from thoth.application.services.verification_trace_compute import (
    _chain as _chain,
)
from thoth.application.services.verification_trace_compute import (
    _children as _children,
)
from thoth.application.services.verification_trace_compute import (
    _Context as _Context,
)
from thoth.application.services.verification_trace_compute import (
    _criterion_revision as _criterion_revision,
)
from thoth.application.services.verification_trace_compute import (
    _digest as _digest,
)
from thoth.application.services.verification_trace_compute import (
    _ids_of as _ids_of,
)
from thoth.application.services.verification_trace_compute import (
    _judge as _judge,
)
from thoth.application.services.verification_trace_compute import (
    _meets as _meets,
)
from thoth.application.services.verification_trace_compute import (
    _requirement_revision as _requirement_revision,
)
from thoth.application.services.verification_trace_compute import (
    _requirement_state as _requirement_state,
)
from thoth.application.services.verification_trace_compute import (
    _requirements_of as _requirements_of,
)
from thoth.application.services.verification_trace_compute import (
    _select as _select,
)
from thoth.application.services.verification_trace_compute import (
    _show as _show,
)
from thoth.application.services.verification_trace_compute import (
    _value_to_compare as _value_to_compare,
)
from thoth.application.services.verification_trace_compute import (
    compute_verdicts as compute_verdicts,
)
from thoth.domain.enums import EntityType
from thoth.domain.verification_trace import (
    ChangedDependency,
    Confirmation,
    Currentness,
    SelectionPolicy,
    SubjectKind,
    TraceSet,
    VerdictRevision,
    VerificationTraceRecord,
    VerificationVerdictRecord,
    subject_key,
)

KEY = "verification-trace:project"
REVISION_PREFIX = "trace-verdict:"
_Built = tuple[VerificationTraceRecord, tuple[VerdictRevision, ...]]


class TraceError(ValueError):
    pass


class TraceConflict(TraceError):
    pass


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
