"""Pause/resume/stop the persisted research continuation with current owner authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from thoth.application.services.post_execution_resume import resume_post_execution_learning
from thoth.application.services.request_language import resume_language
from thoth.domain.enums import EntityType, OperationState, ThreadExecutionState
from thoth.domain.operation import OperationRecord
from thoth.domain.research_lease import ResearchLease
from thoth.domain.research_request import (
    ResearchAttempt,
    decode_thread_request,
)
from thoth.protocol.deferred import AcceptedRunning
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


class ResearchControls:
    def __init__(self, host: ResearchThreadHandlers) -> None:
        self.host = host

    def current(
        self, value: dict[str, JsonValue]
    ) -> tuple[ResearchAttempt, OperationRecord] | None:
        project, thread = str(value["project_id"]), str(value["thread_id"])
        self.host.legacy.authorize_before_claim("thread/read", value)
        saved = self.host.records.read(project, EntityType.THREAD, f"request:{thread}")
        if saved is None:
            return None
        request = decode_thread_request(saved[1])
        resume_language(request, value)
        attempt = self.host.records.journal_read(project, request.operation_id, ResearchAttempt)
        operation = self.host.operations.read(request.operation_id)
        if attempt is None or operation is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "RESEARCH_CONTINUATION_MISSING")
        self.require_owner(operation)
        return attempt, operation

    def require_owner(self, operation: OperationRecord) -> None:
        self.host.operation_access.require_execution_owner(operation)

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        found = self.current(value)
        if found is None:
            return await self.host.legacy.pause(value)
        attempt, operation = found
        if operation.state != OperationState.RUNNING:
            return {"pause_accepted": False, "reason": "OPERATION_ALREADY_TERMINAL"}
        project, thread_id = str(value["project_id"]), str(value["thread_id"])
        with self.host.records.ledger.transaction():
            thread = self.host.threads.read(thread_id)
            if thread is None:
                raise ValueError("THREAD_NOT_FOUND")
            lease = self.host.records.journal_read(project, f"lease:{thread_id}", ResearchLease)
            active = lease is not None and lease.state == "HELD"
            state = ThreadExecutionState.PAUSE_PENDING if active else ThreadExecutionState.PAUSED
            status = "PAUSE_REQUESTED" if active else "PAUSED"
            # Already in the requested pause (thread and attempt both): write nothing.
            repeated = thread.execution_state == state and attempt.status == status
            if not repeated:
                if not self.host.threads.update(
                    thread.model_copy(
                        update={"execution_state": state, "revision": thread.revision + 1}
                    ),
                    expected_revision=thread.revision,
                ):
                    raise ValueError("THREAD_REVISION_CHANGED")
                self.host.records.journal(
                    project, operation.operation_id, attempt.model_copy(update={"status": status})
                )
        return {
            "project_id": project,
            "thread_id": thread_id,
            "operation_id": operation.operation_id,
            "execution_state": state.value,
            "pause_accepted": True,
            **(
                {"unchanged": True, "unchanged_reason": "THREAD_ALREADY_PAUSED"} if repeated else {}
            ),
        }

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        found = self.current(value)
        if found is None:
            return await self.host.legacy.resume(value)
        attempt, operation = found
        if operation.state == OperationState.SUCCEEDED or (
            operation.state == OperationState.RUNNING
            and attempt.external_effect_state == "RETURNED"
        ):
            learning = await resume_post_execution_learning(
                self.host, attempt, operation, resume_pause=True
            )
            if learning is not None:
                return {
                    "resume_allowed": True,
                    "execution_repeated": False,
                    "post_execution_learning": learning.model_dump(mode="json"),
                }
        if operation.state != OperationState.RUNNING:
            return {"resume_allowed": False, "reason": "OPERATION_ALREADY_TERMINAL"}
        if attempt.external_effect_state != "NONE":
            return {
                "resume_allowed": False,
                "reason": "EXTERNAL_EFFECT_RECONCILIATION_REQUIRED",
                "external_effect_ref": attempt.external_effect_ref,
                "external_effect_state": attempt.external_effect_state,
            }
        thread_id = str(value["thread_id"])
        with self.host.records.ledger.transaction():
            thread = self.host.threads.read(thread_id)
            if thread is None:
                raise ValueError("THREAD_NOT_FOUND")
            lease = self.host.records.journal_read(
                operation.project_id, f"lease:{thread_id}", ResearchLease
            )
            if (
                attempt.status == "RUNNING"
                and lease is not None
                and lease.state == "HELD"
                and lease.expires_at > self.host.records.clock.now()
                and self.host.leases.worker.is_alive(
                    lease.process_id, lease.process_marker, lease.worker_id
                )
                is True
            ):
                # Already running under a live worker: no write and no second dispatch. (A dead,
                # unknown or expired holder still takes the recovery path below.)
                return {
                    **self.host.accepted_value(attempt),
                    "resume_allowed": True,
                    "execution_state": thread.execution_state.value,
                    "unchanged": True,
                    "unchanged_reason": "THREAD_ALREADY_RESUMED",
                }
            state = (
                ThreadExecutionState.RUNNING
                if lease is not None and lease.state == "HELD"
                else ThreadExecutionState.IDLE
            )
            if not self.host.threads.update(
                thread.model_copy(
                    update={"execution_state": state, "revision": thread.revision + 1}
                ),
                expected_revision=thread.revision,
            ):
                raise ValueError("THREAD_REVISION_CHANGED")
            attempt = attempt.model_copy(update={"status": "RUNNING"})
            self.host.records.journal(operation.project_id, operation.operation_id, attempt)
        resumed = self.host.replay_attempt(attempt, operation)
        return AcceptedRunning(
            {**resumed.value, "resume_allowed": True, "execution_state": state.value},
            resumed.continue_execution,
            target_operation=operation,
        )

    async def stop(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        found = self.current(value)
        result = await self.host.legacy.stop(value)
        if found is not None:
            attempt, operation = found
            if result.get("unchanged") is True:
                if operation.state != OperationState.RUNNING:
                    result["cancelled"] = False  # this request cancelled nothing new
                    return result
                # Stopped thread, request still running (a first stop was cut off before it
                # cancelled): finish that cancel now, as a normal stop.
                del result["unchanged"], result["unchanged_reason"]
            self.host.operations.cancel(
                operation.operation_id, completed_at=self.host.records.clock.now()
            )
            self.host.records.journal(
                operation.project_id,
                operation.operation_id,
                attempt.model_copy(
                    update={"status": "CANCEL_REQUESTED", "remote_observation": "UNKNOWN"}
                ),
            )
            result.update(
                cancelled=True,
                cancel_operation_id=operation.operation_id,
                remote_observation="UNKNOWN",
            )
        return result
