"""Pure read projections for progress, coverage, deltas and review queues."""

from collections.abc import Mapping

from thoth.application.services.research_criterion_delta import CriterionView
from thoth.application.services.research_decision_delta import decision_delta as decision_delta
from thoth.application.services.research_followup_calculations import (
    ResultManifest,
    _basis_currentness,
    _coverage_matrix,
    _next_action,
    _progress_items,
    _progress_state,
    _recorded_checks,
    _remaining_gaps,
    _unknowns,
)
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.resource_scope_read_context import scope_read_transaction
from thoth.domain.auth import authenticated_data_scope_allows
from thoth.domain.enums import EntityType
from thoth.domain.evidence_requirements import (
    RequirementSetRevision,
)
from thoth.domain.operation import OperationRecord
from thoth.domain.project import WorkThread
from thoth.domain.research_codec import decode_current_result_manifest, decode_research_record
from thoth.domain.research_followup import (
    CoverageMatrix,
    NextUserAction,
    ProjectReviewItem,
    ProjectReviewList,
    ProjectReviewListInput,
    UserProgressSummary,
)
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_request import (
    CurrentResultManifestV21,
    ResearchAttempt,
    ThreadRequestRevision,
)
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.thread import ThreadStorePort


def project_thread_followup(
    *,
    ledger: LedgerPort,
    access: ResourceAccessPort,
    request_ref: RevisionRef,
    request: ThreadRequestRevision,
    result_ref: RevisionRef | None,
    manifest: ResultManifest | None,
    attempt: ResearchAttempt | None,
    operation_state: str,
    currentness_state: Mapping[str, object],
) -> tuple[UserProgressSummary, CoverageMatrix, NextUserAction]:
    records = _related_records(ledger, access, request_ref.project_id, manifest)
    currentness = _basis_currentness(currentness_state)
    coverage = _coverage_matrix(request_ref, records)
    next_action = _next_action(
        request_ref=request_ref,
        result_ref=result_ref,
        manifest=manifest,
        currentness=currentness,
        coverage=coverage,
        operation_state=operation_state,
    )
    summary = UserProgressSummary(
        request_revision_digest=request_ref.revision_digest,
        result_revision_digest=None if result_ref is None else result_ref.revision_digest,
        state=_progress_state(
            manifest=manifest,
            attempt=attempt,
            operation_state=operation_state,
            currentness_state=currentness.state,
            coverage=coverage,
        ),
        currentness=currentness,
        progress_items=_progress_items(attempt, manifest),
        recorded_checks=_recorded_checks(records, coverage),
        remaining_gaps=_remaining_gaps(manifest, records, coverage),
        unknowns=_unknowns(manifest, records, coverage),
        next_user_action=next_action,
    )
    return summary, coverage, next_action


class CriterionViewReader:
    """Reads a stored result's criteria and their coverage rows, within the reader's access."""

    def __init__(self, *, ledger: LedgerPort, access: ResourceAccessPort) -> None:
        self._ledger, self._access = ledger, access

    def read(
        self, project_id: str, manifest: Mapping[str, object] | None
    ) -> tuple[CriterionView, ...]:
        if manifest is None:
            return ()
        try:
            typed = decode_current_result_manifest(dict(manifest))
        except ValueError:
            return ()
        records = _related_records(self._ledger, self._access, project_id, typed)
        entry = records.get("RequirementSetRevision")
        if entry is None or not isinstance(entry[1], RequirementSetRevision):
            return ()
        matrix = _coverage_matrix(typed.request_ref, records)
        rows = {row.requirement_id: row for row in matrix.rows}
        return tuple(
            CriterionView(requirement=requirement, row=rows[requirement.requirement_id])
            for requirement in entry[1].requirements
            if requirement.requirement_id in rows
        )


class ProjectReviewReader:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
        governance: GovernanceStorePort,
        operations: OperationStorePort,
        access: ResourceAccessPort,
        freshness: ResearchFreshnessService,
    ) -> None:
        self.ledger = ledger
        self.projects = projects
        self.threads = threads
        self.governance = governance
        self.operations = operations
        self.access = access
        self.freshness = freshness

    def list(self, value: ProjectReviewListInput) -> ProjectReviewList:
        # One read transaction and one approval memo for the whole page: each thread's records share
        # ancestors, so approving them once per request is enough.
        with self.ledger.transaction(), scope_read_transaction():
            return self._list(value)

    def _list(self, value: ProjectReviewListInput) -> ProjectReviewList:
        start = _decode_cursor(value.cursor)
        threads = sorted(
            self.threads.list(value.project_id),
            key=lambda item: (
                "" if item.updated_at is None else item.updated_at.isoformat(),
                item.thread_id,
            ),
            reverse=True,
        )
        items: list[ProjectReviewItem] = []
        index = start
        while index < len(threads) and len(items) < value.limit:
            item = self._thread_item(threads[index])
            if item is not None:
                items.append(item)
            index += 1
        next_cursor = str(index) if index < len(threads) else None
        return ProjectReviewList(
            project_id=value.project_id,
            items=tuple(items),
            next_cursor=next_cursor,
            coverage="CONTINUATION" if next_cursor is not None else "COMPLETE_PAGE",
        )

    def _thread_item(self, thread: WorkThread) -> ProjectReviewItem | None:
        if not authenticated_data_scope_allows(thread.scope):
            return None
        heads = self.ledger.read_heads(thread.project_id)
        request_digest = heads.get(f"THREAD:request:{thread.thread_id}")
        result_digest = heads.get(f"DECISION_OBJECT:result:{thread.thread_id}")
        if request_digest is None or not self.access.may_read_revision(
            thread.project_id, request_digest
        ):
            return None
        if result_digest is not None and not self.access.may_read_revision(
            thread.project_id, result_digest
        ):
            return None
        current = _read_head(
            self.ledger,
            thread.project_id,
            EntityType.THREAD,
            f"request:{thread.thread_id}",
            request_digest,
        )
        if current is None:
            return None
        request = ThreadRequestRevision.model_validate(current[1])
        result = _read_head(
            self.ledger,
            thread.project_id,
            EntityType.DECISION_OBJECT,
            f"result:{thread.thread_id}",
            result_digest,
        )
        manifest = None if result is None else decode_current_result_manifest(result[1])
        basis = manifest.research_basis if isinstance(manifest, CurrentResultManifestV21) else None
        currentness = self.freshness.evaluate_result(
            thread.project_id,
            basis,
            operation_id=None if manifest is None else manifest.operation_id,
            completion=None if manifest is None else manifest.completion,
        )
        records = _related_records(self.ledger, self.access, thread.project_id, manifest)
        matrix = _coverage_matrix(current[0], records)
        action = _next_action(
            request_ref=current[0],
            result_ref=None if result is None else result[0],
            manifest=manifest,
            currentness=currentness,
            coverage=matrix,
            operation_state=_operation_state(self.operations, request.operation_id),
        )
        if action.action_type == "NONE":
            return None
        reason_codes = tuple(
            dict.fromkeys(
                (*currentness.reasons, *matrix.summary.reason_codes, *action.reason_codes)
            )
        )
        priority = (
            "HIGH"
            if currentness.state in {"INVALIDATED", "UNAVAILABLE"}
            or "answer" in matrix.summary.hold_targets
            else "MEDIUM"
            if currentness.state != "CURRENT" or matrix.summary.unresolved
            else "LOW"
        )
        return ProjectReviewItem(
            item_id=f"{thread.thread_id}:{current[0].revision_digest}",
            project_id=thread.project_id,
            thread_id=thread.thread_id,
            request_revision_digest=current[0].revision_digest,
            result_revision_digest=None if result is None else result[0].revision_digest,
            title=thread.display_name or request.effective_question[:120],
            priority=priority,
            reason_codes=reason_codes,
            currentness=currentness,
            next_user_action=action,
        )


def _related_records(
    ledger: LedgerPort,
    access: ResourceAccessPort,
    project_id: str,
    manifest: ResultManifest | None,
) -> dict[str, tuple[RevisionRef, object]]:
    records: dict[str, tuple[RevisionRef, object]] = {}
    if manifest is None:
        return records
    for ref in manifest.record_refs:
        if ref.project_id != project_id or not access.may_read_revision(
            project_id, ref.revision_digest
        ):
            continue
        revision = ledger.read_revision_by_digest(project_id, ref.revision_digest)
        snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            continue
        record = decode_research_record(dict(snapshot.content))
        records.setdefault(str(snapshot.content.get("record_kind")), (ref, record))
    return records


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


def _read_head(
    ledger: LedgerPort,
    project_id: str,
    entity_type: EntityType,
    entity_id: str,
    digest: str | None,
) -> tuple[RevisionRef, dict[str, object]] | None:
    if digest is None:
        return None
    revision = ledger.read_revision_by_digest(project_id, digest)
    if revision is None or revision.entity_type != entity_type or revision.entity_id != entity_id:
        return None
    snapshot = ledger.read_snapshot(revision.snapshot_id)
    if snapshot is None:
        return None
    return (
        RevisionRef(
            project_id=project_id,
            entity_type=entity_type.value,
            entity_id=entity_id,
            revision_id=revision.revision_id,
            revision_digest=digest,
        ),
        dict(snapshot.content),
    )


def _operation_state(operations: OperationStorePort, operation_id: str) -> str:
    operation: OperationRecord | None = operations.read(operation_id)
    return "UNKNOWN" if operation is None else operation.state.value
