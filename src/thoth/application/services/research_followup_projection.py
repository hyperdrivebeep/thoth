"""Pure read projections for progress, coverage, deltas and review queues."""

from collections.abc import Iterable, Mapping
from typing import Literal, cast

from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.revision_diff import semantic_diff
from thoth.domain.auth import authenticated_data_scope_allows
from thoth.domain.enums import EntityType
from thoth.domain.evidence_requirements import (
    CoverageAssessment,
    EvidenceRequirement,
    RequirementAssessment,
    RequirementSetRevision,
    SemanticReviewRecord,
)
from thoth.domain.operation import OperationRecord
from thoth.domain.project import WorkThread
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_codec import decode_current_result_manifest, decode_research_record
from thoth.domain.research_followup import (
    CoverageMatrix,
    CoverageMatrixRow,
    CoverageMatrixSummary,
    DecisionDelta,
    DecisionDeltaGroup,
    NextUserAction,
    ProjectReviewItem,
    ProjectReviewList,
    ProjectReviewListInput,
    ResultIdentity,
    UserProgressSummary,
)
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_request import (
    CurrentResultManifest,
    CurrentResultManifestV21,
    ResearchAttempt,
    ThreadRequestRevision,
)
from thoth.domain.revision import SemanticDiffEntry
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.thread import ThreadStorePort

ResultManifest = CurrentResultManifest | CurrentResultManifestV21
ProgressState = Literal[
    "PENDING_OR_NOT_PRODUCED",
    "IN_PROGRESS",
    "COMPLETE",
    "NEEDS_REVIEW",
    "HOLD",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
]
DeltaKind = Literal["CONTENT", "EVIDENCE", "CONDITION", "STATUS", "ACTION", "OTHER"]


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


def _coverage_matrix(
    request_ref: RevisionRef,
    records: Mapping[str, tuple[RevisionRef, object]],
) -> CoverageMatrix:
    requirement_entry = records.get("RequirementSetRevision")
    coverage_entry = records.get("CoverageAssessment")
    requirement_set = (
        requirement_entry[1] if requirement_entry is not None else None
    )
    coverage = coverage_entry[1] if coverage_entry is not None else None
    if not isinstance(requirement_set, RequirementSetRevision):
        return CoverageMatrix(
            request_revision_digest=request_ref.revision_digest,
            coverage_revision_digest=None
            if coverage_entry is None
            else coverage_entry[0].revision_digest,
            availability="PARTIAL" if coverage_entry is not None else "UNAVAILABLE",
            reason_codes=("REQUIREMENT_SET_UNAVAILABLE",),
        )
    if requirement_entry is None:
        raise ValueError("REQUIREMENT_ENTRY_MISSING")
    requirement_ref = requirement_entry[0]
    if not isinstance(coverage, CoverageAssessment):
        rows = tuple(
            CoverageMatrixRow(
                requirement_id=req.requirement_id,
                target=req.target,
                question=req.question,
                status="NOT_ASSESSED",
                applicability=req.applicability,
                relation="NOT_ASSESSED",
                validation="NOT_ASSESSED",
                blocker=req.blocker,
                reason_codes=("COVERAGE_UNAVAILABLE",),
            )
            for req in requirement_set.requirements
        )
        return CoverageMatrix(
            request_revision_digest=request_ref.revision_digest,
            requirement_set_revision_digest=requirement_ref.revision_digest,
            rows=rows,
            summary=_matrix_summary(rows, {}, ("COVERAGE_UNAVAILABLE",)),
            availability="PARTIAL",
            reason_codes=("COVERAGE_UNAVAILABLE",),
        )
    if coverage_entry is None:
        raise ValueError("COVERAGE_ENTRY_MISSING")
    reviews = _semantic_reviews(records)
    assessments = {item.requirement_id: item for item in coverage.assessments}
    rows = tuple(
        _matrix_row(req, assessments.get(req.requirement_id), reviews)
        for req in requirement_set.requirements
    )
    reason_codes = tuple(dict.fromkeys((*coverage.reasons, *coverage.allowed_next_steps)))
    return CoverageMatrix(
        request_revision_digest=request_ref.revision_digest,
        requirement_set_revision_digest=requirement_ref.revision_digest,
        coverage_revision_digest=coverage_entry[0].revision_digest,
        rows=rows,
        summary=_matrix_summary(rows, coverage.gates, reason_codes),
        availability="AVAILABLE",
        reason_codes=reason_codes,
    )


def _matrix_row(
    requirement: EvidenceRequirement,
    assessment: RequirementAssessment | None,
    reviews: Mapping[str, SemanticReviewRecord],
) -> CoverageMatrixRow:
    if assessment is None:
        return CoverageMatrixRow(
            requirement_id=requirement.requirement_id,
            target=requirement.target,
            question=requirement.question,
            status="NOT_ASSESSED",
            applicability=requirement.applicability,
            relation="NOT_ASSESSED",
            validation="NOT_ASSESSED",
            blocker=requirement.blocker,
            reason_codes=("ASSESSMENT_UNAVAILABLE",),
        )
    review_refs = assessment.review_refs
    evidence_refs: list[str] = []
    for ref in review_refs:
        review = reviews.get(ref)
        if review is not None:
            evidence_refs.extend(review.candidate.evidence_refs)
    return CoverageMatrixRow(
        requirement_id=requirement.requirement_id,
        target=requirement.target,
        question=requirement.question,
        status=assessment.resolution,
        applicability=assessment.applicability,
        relation=assessment.relation,
        validation=assessment.validation,
        blocker=assessment.blocker,
        review_refs=review_refs,
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        reason_codes=()
        if assessment.resolution == "SATISFIED"
        else tuple(dict.fromkeys((assessment.blocker, assessment.validation))),
    )


def _semantic_reviews(
    records: Mapping[str, tuple[RevisionRef, object]],
) -> dict[str, SemanticReviewRecord]:
    reviews: dict[str, SemanticReviewRecord] = {}
    for _kind, (_ref, record) in records.items():
        if isinstance(record, SemanticReviewRecord):
            reviews[record.candidate.requirement_id] = record
    return reviews


def _matrix_summary(
    rows: Iterable[CoverageMatrixRow],
    gates: Mapping[str, str],
    reason_codes: tuple[str, ...],
) -> CoverageMatrixSummary:
    materialized = tuple(rows)
    return CoverageMatrixSummary(
        satisfied=sum(1 for row in materialized if row.status == "SATISFIED"),
        unresolved=sum(1 for row in materialized if row.status == "UNRESOLVED"),
        not_applicable=sum(1 for row in materialized if row.status == "NOT_APPLICABLE"),
        not_assessed=sum(1 for row in materialized if row.status == "NOT_ASSESSED"),
        hold_targets=tuple(target for target, state in gates.items() if state == "HOLD"),
        reason_codes=reason_codes,
    )


def _next_action(
    *,
    request_ref: RevisionRef,
    result_ref: RevisionRef | None,
    manifest: ResultManifest | None,
    currentness: BasisCurrentness,
    coverage: CoverageMatrix,
    operation_state: str,
) -> NextUserAction:
    basis: dict[str, object] = {
        "request_revision_digest": request_ref.revision_digest,
        "result_revision_digest": None if result_ref is None else result_ref.revision_digest,
    }
    if operation_state in {"FAILED", "CANCELLED"}:
        return NextUserAction(
            action_type="OPEN_RESULT_DETAIL",
            label="Review execution outcome",
            reason_codes=(operation_state,),
            target=basis,
            basis=basis,
        )
    if manifest is None:
        return NextUserAction(
            action_type="OPEN_RESULT_DETAIL",
            label="Wait for or inspect the current research attempt",
            reason_codes=("RESULT_NOT_PRODUCED",),
            target=basis,
            basis=basis,
        )
    if currentness.state != "CURRENT":
        return NextUserAction(
            action_type="REVIEW_CURRENTNESS",
            label="Review result currentness before using this answer",
            reason_codes=currentness.reasons,
            target=basis,
            basis=basis,
        )
    if (
        coverage.summary.unresolved
        or coverage.summary.not_assessed
        or coverage.summary.hold_targets
    ):
        return NextUserAction(
            action_type="REVIEW_GAPS",
            label="Review unresolved evidence gaps",
            reason_codes=tuple(
                dict.fromkeys(
                    (
                        *coverage.summary.reason_codes,
                        *coverage.summary.hold_targets,
                    )
                )
            ),
            target=basis,
            basis=basis,
        )
    return NextUserAction(action_type="NONE", label="No recorded review action", basis=basis)


def _progress_state(
    *,
    manifest: ResultManifest | None,
    attempt: ResearchAttempt | None,
    operation_state: str,
    currentness_state: str,
    coverage: CoverageMatrix,
) -> ProgressState:
    if operation_state == "FAILED":
        return "FAILED"
    if operation_state == "CANCELLED":
        return "CANCELLED"
    if manifest is None:
        if attempt is not None and operation_state == "RUNNING":
            return "IN_PROGRESS"
        return "PENDING_OR_NOT_PRODUCED"
    if manifest.phase == "HOLD" or coverage.summary.hold_targets:
        return "HOLD"
    if (
        currentness_state != "CURRENT"
        or coverage.summary.unresolved
        or coverage.summary.not_assessed
    ):
        return "NEEDS_REVIEW"
    if operation_state == "SUCCEEDED" and manifest.completion == "TERMINAL":
        return "COMPLETE"
    return "UNKNOWN"


def _progress_items(
    attempt: ResearchAttempt | None, manifest: ResultManifest | None
) -> tuple[str, ...]:
    items: list[str] = []
    if attempt is not None:
        items.extend(ref.entity_id for ref in attempt.completed_stage_refs)
    if manifest is not None:
        items.append(f"result:{manifest.completion.lower()}")
    return tuple(dict.fromkeys(items))


def _recorded_checks(
    records: Mapping[str, tuple[RevisionRef, object]], coverage: CoverageMatrix
) -> tuple[str, ...]:
    checks: list[str] = []
    if "RequirementSetRevision" in records:
        checks.append("requirements_recorded")
    if "CoverageAssessment" in records:
        checks.append("coverage_assessed")
    checks.extend(f"requirement:{row.requirement_id}:{row.status}" for row in coverage.rows)
    return tuple(checks)


def _remaining_gaps(
    manifest: ResultManifest | None,
    records: Mapping[str, tuple[RevisionRef, object]],
    coverage: CoverageMatrix,
) -> tuple[str, ...]:
    gaps: list[str] = []
    if manifest is not None:
        gaps.extend(manifest.gaps)
        gaps.extend(manifest.next_steps)
    coverage_entry = records.get("CoverageAssessment")
    if coverage_entry is not None and isinstance(coverage_entry[1], CoverageAssessment):
        assessment = coverage_entry[1]
        gaps.extend(assessment.reasons)
        gaps.extend(assessment.allowed_next_steps)
    gaps.extend(
        row.blocker for row in coverage.rows if row.status in {"UNRESOLVED", "NOT_ASSESSED"}
    )
    return tuple(item for item in dict.fromkeys(gaps) if item)


def _unknowns(
    manifest: ResultManifest | None,
    records: Mapping[str, tuple[RevisionRef, object]],
    coverage: CoverageMatrix,
) -> tuple[str, ...]:
    unknowns: list[str] = []
    if manifest is None:
        unknowns.append("RESULT_NOT_PRODUCED")
    elif not isinstance(manifest, CurrentResultManifestV21):
        unknowns.append("LEGACY_BASIS_UNKNOWN")
    if "RequirementSetRevision" not in records:
        unknowns.append("REQUIREMENT_SET_UNAVAILABLE")
    if "CoverageAssessment" not in records:
        unknowns.append("COVERAGE_UNAVAILABLE")
    if coverage.summary.not_assessed:
        unknowns.append("REQUIREMENT_NOT_ASSESSED")
    return tuple(dict.fromkeys(unknowns))


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
        cast(Mapping[object, object], gates_raw)
        if isinstance(gates_raw, Mapping)
        else empty_gates
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


def _basis_currentness(value: Mapping[str, object]) -> BasisCurrentness:
    return BasisCurrentness.model_validate(value)


def _object_tuple(value: list[object] | tuple[object, ...]) -> tuple[object, ...]:
    return tuple(cast(Iterable[object], value))


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
