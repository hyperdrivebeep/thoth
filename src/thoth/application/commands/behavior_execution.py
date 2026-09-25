"""Bind one behavior snapshot to a normal Thread operation and retain its use receipt."""

import asyncio
from collections.abc import Callable
from typing import Literal

from pydantic import Field, JsonValue

from thoth.application.commands.thread_analysis import (
    ThreadInputRequest,
)
from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.behavior_execution import BehaviorExecutionRecord, BehaviorExposureRecord
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import head_set_digest
from thoth.domain.enums import ThreadExecutionState
from thoth.domain.evaluation_run import EvaluationRunError, sealed_payload
from thoth.domain.research_execution import research_work
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadEntryPort, ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class BehaviorThreadEntry:
    def __init__(
        self,
        delegate: ThreadEntryPort,
        snapshots: BehaviorSnapshotService,
        controls: ControlRecordService,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
        governance: GovernanceStorePort,
    ) -> None:
        self._delegate, self._snapshots, self._controls = delegate, snapshots, controls
        self._ledger, self._clock, self._ids = ledger, clock, ids
        self._projects, self._threads = projects, threads
        self._governance = governance

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        self._delegate.authorize_before_claim(method, value)

    def _persist(self, record: BehaviorExecutionRecord) -> None:
        with self._ledger.transaction():
            self._controls.create(
                project_id=record.project_id,
                namespace="BEHAVIOR_RUNTIME",
                record_type="EXECUTION",
                state=record.state,
                record_id=record.execution_id,
                payload=record.model_dump(mode="python"),
            )

    async def analyze(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return await self._execute(value)
        except BehaviorPolicyError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
            ) from exc
        except RpcApplicationError as exc:
            if isinstance(exc.__cause__, BehaviorPolicyError):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    exc.__cause__.code,
                    data={"reason_code": exc.__cause__.code},
                ) from exc
            raise

    async def _execute(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ThreadInputRequest.model_validate(value)
        project, thread = request.project_id, request.thread_id
        stored_thread = self._threads.read(thread)
        if (
            self._projects.read(project) is None
            or stored_thread is None
            or stored_thread.project_id != project
            or stored_thread.execution_state == ThreadExecutionState.RUNNING
        ):
            return await self._delegate.analyze(value)
        execution_id = self._ids.new("behavior-execution")
        with self._ledger.transaction():
            policy = self._governance.read_policy(project)
            if policy is None:
                raise BehaviorPolicyError("BEHAVIOR_POLICY_NOT_FOUND")
            actor = current_authenticated_actor()
            selection = self._snapshots.prepare_work(project, thread, execution_id)
            context = BehaviorWorkContext(
                project,
                selection.snapshots,
                shadows=selection.shadows,
                control=self._snapshots.control,
            )
            record = BehaviorExecutionRecord.model_validate(
                sealed_payload(
                    "BEHAVIOR_EXECUTION_RECORD",
                    "receipt_digest",
                    {
                        "execution_id": execution_id,
                        "project_id": project,
                        "thread_id": thread,
                        "actor_ref": "local:operator" if actor is None else actor.actor_id,
                        "session_ref": None if actor is None else actor.session_id,
                        "input_head_set_digest": head_set_digest(self._ledger.read_heads(project)),
                        "project_policy_digest": policy.policy_digest,
                        "state": "RUNNING",
                        "snapshots": (*context.snapshots, *context.shadows),
                        "uses": (),
                        "started_at": self._clock.now(),
                        "finished_at": None,
                        "reason_code": None,
                        "semantic_truth_certified": False,
                    },
                )
            )
            self._persist(record)
        state, reason = "COMPLETED", None
        try:
            with behavior_work_scope(context):
                work = research_work.get()
                if work is not None and work.prepare is not None:
                    await work.prepare()
                result = await self._delegate.analyze(value)
            if result.get("queued") is True:
                state, reason = "HELD", "BEHAVIOR_WORK_QUEUED_NOT_EXECUTED"
        except asyncio.CancelledError:
            state, reason = "CANCELLED", "BEHAVIOR_EXECUTION_CANCELLED"
            raise
        except Exception as exc:
            typed = exc if isinstance(exc, BehaviorPolicyError) else exc.__cause__
            state, reason = (
                "HELD",
                typed.code if isinstance(typed, BehaviorPolicyError) else "BEHAVIOR_EXECUTION_HELD",
            )
            raise
        finally:
            data = record.model_dump(mode="python", exclude={"receipt_digest"})
            data.update(
                state=state,
                reason_code=reason,
                uses=tuple(context.uses),
                finished_at=self._clock.now(),
            )
            record = BehaviorExecutionRecord.model_validate(
                sealed_payload(
                    "BEHAVIOR_EXECUTION_RECORD",
                    "receipt_digest",
                    data,
                )
            )
            with self._ledger.transaction():
                self._persist(record)
                self._snapshots.finish_work(record)
        return (
            result
            if result.get("queued") is True
            else {**result, "behavior_execution": record.model_dump(mode="json")}
        )


class ExposureRuntimeInput(DomainModel):
    project_id: str
    exposure_id: str


class ExposureStartInput(ExposureRuntimeInput):
    expected_revision: int = Field(ge=1)


class ExposureDecisionInput(ExposureStartInput):
    decision: Literal["APPROVE", "REJECT"]
    approved_digest: str = Field(min_length=64, max_length=64)
    actor_ref: str
    role_assignment_ref: str


class ExposureRollbackInput(ExposureStartInput):
    actor_ref: str
    role_assignment_ref: str


class BehaviorExposureCommands:
    def __init__(self, service: ImprovementExposureService) -> None:
        self._service = service

    @staticmethod
    def _call(operation: Callable[[], BehaviorExposureRecord]) -> dict[str, JsonValue]:
        try:
            record = operation()
        except (BehaviorPolicyError, EvaluationRunError) as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
            ) from exc
        return {
            "exposure": record.model_dump(mode="json"),
            "actual_execution_observed": bool(record.observations),
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExposureRuntimeInput.model_validate(value)
        return self._call(lambda: self._service.read(request.project_id, request.exposure_id))

    async def start(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExposureStartInput.model_validate(value)
        return self._call(
            lambda: self._service.start(
                request.project_id, request.exposure_id, expected_revision=request.expected_revision
            )
        )

    async def decide(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExposureDecisionInput.model_validate(value)
        return self._call(
            lambda: self._service.decide(
                request.project_id,
                request.exposure_id,
                expected_revision=request.expected_revision,
                approved_digest=request.approved_digest,
                actor_ref=request.actor_ref,
                role_ref=request.role_assignment_ref,
                decision=request.decision,
            )
        )

    async def complete(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExposureStartInput.model_validate(value)
        return self._call(
            lambda: self._service.complete(
                request.project_id, request.exposure_id, expected_revision=request.expected_revision
            )
        )

    async def rollback(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExposureRollbackInput.model_validate(value)
        return self._call(
            lambda: self._service.rollback(
                request.project_id,
                request.exposure_id,
                expected_revision=request.expected_revision,
                actor_ref=request.actor_ref,
                role_ref=request.role_assignment_ref,
            )
        )
