"""Seal one owned execution failure without publishing another research revision."""

from typing import cast

from pydantic import JsonValue

from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType, OperationState, ThreadExecutionState
from thoth.domain.research_failure import ResearchFailureCause, ResearchFailureRecord
from thoth.domain.research_lease import ResearchLease, ResearchLeaseLost
from thoth.domain.research_request import ResearchAttempt, ThreadRequestRevision
from thoth.ports.operation import OperationStorePort
from thoth.ports.thread import ThreadStorePort


class ResearchTerminationService:
    def __init__(
        self, records: RequestRecords, operations: OperationStorePort, threads: ThreadStorePort
    ) -> None:
        self.records, self.operations, self.threads = records, operations, threads

    def finish_failure(
        self,
        attempt: ResearchAttempt,
        request: ThreadRequestRevision,
        primary: ResearchFailureCause,
        secondary: ResearchFailureCause | None = None,
    ) -> ResearchFailureRecord | None:
        project, op_id = request.project_id, attempt.operation_id
        key = f"failure:{op_id}:{attempt.attempt_epoch}"
        with self.records.ledger.transaction():
            prior = self.records.journal_read(project, key, ResearchFailureRecord)
            if prior is not None:
                return prior
            op = self.operations.read(op_id)
            lease = self.records.journal_read(project, f"lease:{request.thread_id}", ResearchLease)
            current = self.records.read(project, EntityType.THREAD, f"request:{request.thread_id}")
            if op is None or op.state != OperationState.RUNNING:
                return None
            if (
                op.project_id != project
                or op_id != request.operation_id
                or (op.owner_actor_id, op.owner_session_id)
                != (attempt.owner_actor_id, attempt.owner_session_id)
                or current is None
                or current[0] != attempt.request_ref
                or lease is None
                or (
                    lease.operation_id,
                    lease.request_digest,
                    lease.epoch,
                    lease.worker_id,
                    lease.state,
                )
                != (
                    op_id,
                    attempt.request_ref.revision_digest,
                    attempt.attempt_epoch,
                    attempt.worker_id,
                    "HELD",
                )
            ):
                raise ResearchLeaseLost("ATTEMPT_LEASE_FENCED")
            latest = self.records.journal_read(project, op_id, ResearchAttempt) or attempt
            failure = ResearchFailureRecord(
                project_id=project,
                thread_id=request.thread_id,
                operation_id=op_id,
                request_ref=attempt.request_ref,
                attempt_epoch=attempt.attempt_epoch,
                phase=latest.phase,
                primary=primary,
                secondary=secondary,
                last_checkpoint_ref=latest.checkpoint_ref,
                remote_observation=latest.remote_observation,
                created_at=self.records.clock.now(),
            )
            self.records.journal(project, key, failure, "FAILED")
            self.operations.fail(
                op_id,
                cast(
                    dict[str, JsonValue],
                    {
                        "code": -32603,
                        "message": "research execution failed",
                        "data": {
                            "reason_code": primary.reason_code,
                            "failure": failure.model_dump(mode="json"),
                        },
                    },
                ),
                completed_at=failure.created_at,
            )
            thread = self.threads.read(request.thread_id)
            if (
                thread is not None
                and thread.execution_state == ThreadExecutionState.RUNNING
                and not self.threads.update(
                    thread.model_copy(
                        update={
                            "execution_state": ThreadExecutionState.IDLE,
                            "revision": thread.revision + 1,
                        }
                    ),
                    expected_revision=thread.revision,
                )
            ):
                raise RuntimeError("RESEARCH_TERMINAL_THREAD_CONFLICT")
            cursor = latest.event_cursor
            if self.records.events is not None:
                event = self.records.events.append(
                    project_id=project,
                    operation_id=op_id,
                    event_type="research.failed",
                    payload={
                        "reason_code": primary.reason_code,
                        "failure_key": key,
                        "attempt_epoch": attempt.attempt_epoch,
                    },
                )
                cursor = event.event_id
            self.records.journal(
                project,
                op_id,
                latest.model_copy(update={"status": "FAILED", "event_cursor": cursor}),
                "FAILED",
            )
            return failure
