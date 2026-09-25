"""Strict restore publication: CAS failure rolls back every domain participant."""

from collections.abc import Callable

from pydantic import JsonValue

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.restore_planner import RestorePlan, RestorePlanner
from thoth.application.services.revision_service import CommitDisposition, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind
from thoth.domain.operation import OperationRecord
from thoth.domain.restore import (
    RestoreApplyInput,
    RestoreApplyResult,
    RestoreError,
    RestoreReceiptV1,
)
from thoth.domain.revision import RevisionChangeSet, SemanticRevision, StagedRevision
from thoth.ports.journal import JournalPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class RestorePublication:
    def __init__(
        self,
        planner: RestorePlanner,
        commits: RevisionCommitService,
        baselines: BaselineService,
        controls: ControlRecordService,
        operations: OperationStorePort,
        events: JournalPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self.planner, self.commits, self.baselines, self.controls = (
            planner,
            commits,
            baselines,
            controls,
        )
        self.operations, self.events, self.clock, self.ids = operations, events, clock, ids

    def apply(
        self,
        request: RestoreApplyInput,
        operation: OperationRecord,
        *,
        legacy: bool = False,
        publication_view: Callable[[RestoreApplyResult], dict[str, JsonValue]] | None = None,
    ) -> RestoreApplyResult:
        if not self.planner.apply_ready:
            raise RestoreError("RESTORE_NOT_READY")
        if (
            request.project_id != request.selection.project_id
            or operation.project_id != request.project_id
        ):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        with self.planner.ledger.transaction():
            plan = self.planner.build(request.selection, require_write=True)
            if plan.preview.basis_digest != request.preview_basis_digest:
                raise RestoreError("RESTORE_PREVIEW_STALE")
            if plan.preview.availability == "NO_CHANGE":
                from thoth.domain.revision import ImpactPropagationPlan

                result = RestoreApplyResult(
                    status="NO_CHANGE", selection=request.selection, impact=ImpactPropagationPlan()
                )
            else:
                result = self._publish(plan, request, operation)
                self.events.append(
                    project_id=request.project_id,
                    operation_id=operation.operation_id,
                    event_type="revision/restored",
                    payload=result.model_dump(mode="json"),
                )
            payload = (
                self.result_payload(result, legacy=legacy)
                if publication_view is None
                else publication_view(result)
            )
            self.events.checkpoint(operation_id=operation.operation_id, payload=payload)
            self.events.append(
                project_id=request.project_id,
                operation_id=operation.operation_id,
                event_type="operation.succeeded",
                payload={"status": result.status},
            )
            self.operations.complete(operation.operation_id, payload, completed_at=self.clock.now())
        return result

    def result_payload(
        self, result: RestoreApplyResult, *, legacy: bool = False
    ) -> dict[str, JsonValue]:
        payload = result.model_dump(mode="json")
        if not legacy:
            return payload
        selected = self.planner.ledger.read_revision_by_digest(
            result.selection.project_id, result.selection.target_revision_digest
        )
        receipt = next(
            (
                receipt
                for receipt in self.planner.ledger.read_receipts(result.selection.project_id)
                if receipt.receipt_id == result.receipt_id
            ),
            None,
        )
        payload["restore"] = {
            "status": result.status,
            "selected_revision_id": None if selected is None else selected.revision_id,
            "restored_revision_id": result.new_revision_id,
            "stale_refs": list(result.impact.stale_refs),
            "recalculate_refs": list(result.impact.recalculate_refs),
            "commit": None
            if receipt is None
            else {
                "disposition": "FAST_FORWARD",
                "committed_revision_ids": [result.new_revision_id],
                "branch_revision_ids": [],
                "before_head_set_digest": receipt.before_head_set_digest,
                "after_head_set_digest": receipt.after_head_set_digest,
                "receipt": receipt.model_dump(mode="json"),
            },
        }
        return payload

    def _publish(
        self, plan: RestorePlan, request: RestoreApplyInput, operation: OperationRecord
    ) -> RestoreApplyResult:
        actor = current_authenticated_actor()
        author = ActorRef(
            actor_id="human:local-user" if actor is None else actor.actor_id,
            kind=ActorKind.HUMAN,
            role="local-operator" if actor is None else actor.role,
            project_id=request.project_id,
            session_id=None if actor is None else actor.session_id,
            role_assignment_ref=None if actor is None else actor.role_assignment_id,
        )
        snapshot = plan.snapshot.model_copy(update={"snapshot_id": self.ids.new("snapshot")})
        payload = dict(
            revision_id=self.ids.new("revision"),
            project_id=request.project_id,
            entity_type=request.selection.entity_type,
            entity_id=request.selection.entity_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=(request.selection.expected_current_head,),
            actor=author,
            reason=request.reason,
            evidence_refs=plan.target.evidence_refs,
            affected_refs=(f"restored-from:{plan.target.revision_id}",),
            created_at=self.clock.now(),
            schema_version="1.0.0",
        )
        revision = SemanticRevision.model_validate(
            {
                **payload,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION", "1.0.0", canonical_payload(payload)
                ),
            }
        )
        commit = self.commits.commit(
            RevisionChangeSet(
                changeset_id=self.ids.new("changeset"),
                project_id=request.project_id,
                expected_heads=plan.basis.expected_heads,
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=plan.basis.impact,
                actor=author,
                reason=request.reason,
            )
        )
        if commit.disposition != CommitDisposition.FAST_FORWARD:
            raise RestoreError("RESTORE_HEAD_CHANGED")
        self.baselines.refresh(
            project_id=request.project_id,
            thread_id=None,
            purpose="Restore",
            propose_candidates=False,
        )
        audit_id = self.ids.new("restore-audit")
        result = RestoreApplyResult(
            status="APPLIED",
            selection=request.selection,
            new_revision_digest=revision.revision_digest,
            new_revision_id=revision.revision_id,
            receipt_id=commit.receipt.receipt_id,
            receipt_digest=commit.receipt.receipt_digest,
            restore_audit_id=audit_id,
            impact=plan.basis.impact,
        )
        audit = RestoreReceiptV1(
            operation_id=operation.operation_id,
            basis=plan.basis,
            preview_basis_digest=request.preview_basis_digest,
            result=result,
        )
        self.controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="RESTORE_RECEIPT",
            record_id=audit_id,
            state="APPLIED",
            payload={
                **audit.model_dump(mode="python"),
                "parent_revision_digests": (
                    request.selection.target_revision_digest,
                    request.selection.expected_current_head,
                    revision.revision_digest,
                ),
            },
        )
        return result
