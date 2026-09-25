"""Historical answers use the selected request, never the latest checkpoint."""

from typing import cast

from thoth.application.services.historical_access_verification import (
    require_historical_operation_access,
)
from thoth.application.services.history_projection import HistoryProjection
from thoth.application.services.research_history_scope import HistoryScopeValidator
from thoth.application.services.resource_scope_read_context import scope_read_transaction
from thoth.application.services.selected_evidence_reader import SelectedEvidenceReader
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_history import (
    HistoricalResultInput,
    HistoricalResultView,
    HistoryCoverage,
    HistoryScope,
)
from thoth.domain.research_request import ResearchAttempt
from thoth.domain.restore import RestoreError
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.operation import OperationStorePort
from thoth.ports.research_history import HistoryRow


class HistoricalResultReader:
    def __init__(
        self,
        projection: HistoryProjection,
        scopes: HistoryScopeValidator,
        operations: OperationStorePort,
        controls: ControlRecordStorePort,
        selected_evidence: SelectedEvidenceReader,
    ) -> None:
        self.projection, self.scopes, self.operations = projection, scopes, operations
        self.controls = controls
        self.selected_evidence = selected_evidence

    def read(self, request: HistoricalResultInput) -> HistoricalResultView:
        self.scopes.require(
            request.project_id,
            HistoryScope(
                project_id=request.project_id,
                thread_id=request.thread_id,
                request_revision_digest=request.request_revision_digest,
            ),
        )
        with self.projection.ledger.transaction(), scope_read_transaction():
            authored = self.scopes.request(request.project_id, request.request_revision_digest)
            operation = self.operations.read(authored.operation_id)
            if operation is not None:
                if operation.project_id != request.project_id:
                    raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
                require_historical_operation_access(self.projection.access, operation)
            candidates = self.projection.ledger.read_revisions(
                request.project_id, "DECISION_OBJECT", f"result:{request.thread_id}"
            )
            selected_digest = request.result_revision_digest
            journal = self.controls.read(
                request.project_id, "RESEARCH_EXECUTION", authored.operation_id
            )
            if selected_digest is None and journal is not None:
                attempt = ResearchAttempt.model_validate(journal.payload)
                if (
                    attempt.request_ref.revision_digest != request.request_revision_digest
                    or attempt.operation_id != authored.operation_id
                ):
                    raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
                if attempt.checkpoint_ref is not None:
                    selected_digest = attempt.checkpoint_ref.revision_digest
            chosen = None
            if selected_digest is None and len(candidates) != 1:
                candidates = ()
            for revision in reversed(candidates):
                if selected_digest and revision.revision_digest != selected_digest:
                    continue
                snapshot = self.projection.ledger.read_snapshot(revision.snapshot_id)
                if snapshot is None:
                    continue
                ref = snapshot.content.get("request_ref")
                if (
                    not isinstance(ref, dict)
                    or cast(dict[str, object], ref).get("revision_digest")
                    != request.request_revision_digest
                ):
                    continue
                if snapshot.content.get("operation_id") != authored.operation_id:
                    raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
                chosen = self.projection.read(
                    request.project_id,
                    HistoryRow(
                        "SEMANTIC_REVISION",
                        revision.revision_id,
                        revision.revision_digest,
                        revision.created_at.isoformat(),
                    ),
                )
                break
            if request.result_revision_digest and chosen is None:
                raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
            result = None if operation is None else operation.result
            if result is not None and (
                result.get("thread_id") != request.thread_id
                or result.get("request_epoch") != authored.request_epoch
            ):
                raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
            manifest = None if chosen is None else chosen[1]
            if manifest is not None:
                result = manifest.get("result")
            return HistoricalResultView.model_validate(
                {
                    "project_id": request.project_id,
                    "thread_id": request.thread_id,
                    "request_revision_digest": request.request_revision_digest,
                    "operation_id": authored.operation_id,
                    "result_revision_digest": None
                    if chosen is None
                    else chosen[0].record_ref.revision_digest,
                    "request": authored.model_dump(mode="json"),
                    "manifest": manifest,
                    "result": result,
                    "error": None if operation is None else operation.error,
                    "operation_state": None if operation is None else operation.state.value,
                    "availability": "AVAILABLE" if chosen is not None else "PARTIAL",
                    "basis_currentness": BasisCurrentness(
                        state="UNKNOWN_BASIS", reasons=("MANIFEST_UNAVAILABLE",)
                    )
                    if chosen is None
                    else chosen[0].currentness,
                    "coverage": HistoryCoverage(
                        association="EXACT" if chosen is not None else "PARTIAL"
                    ),
                    "selected_evidence": self.selected_evidence.read(request, manifest)
                    if request.include_selected_evidence and manifest is not None
                    else None,
                }
            )
