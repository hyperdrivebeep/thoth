from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.auth import (
    authenticated_data_scope_allows,
    bind_authenticated_actor,
    current_authenticated_actor,
)
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import ProjectLifecycle, ThreadExecutionState, ThreadLifecycle
from thoth.domain.project import WorkThread
from thoth.domain.thread_runtime import ThreadActivity, ThreadCheckpoint, ThreadInputRecord
from thoth.ports.investigation import InvestigationStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadAlreadyExistsError, ThreadStorePort
from thoth.ports.thread_runtime import ThreadRuntimeStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectScopedInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ThreadStartInput(ProjectScopedInput):
    thread_id: str | None = Field(default=None, max_length=160)
    cycle_id: str | None = Field(default=None, max_length=160)
    display_name: str = Field(default="", max_length=500)
    problem: str = Field(min_length=1, max_length=20_000)
    scope: dict[str, str] = Field(default_factory=dict)


class ThreadReadInput(ProjectScopedInput):
    thread_id: str = Field(min_length=1, max_length=160)


class ThreadCheckpointReadInput(ThreadReadInput):
    checkpoint_id: str = Field(min_length=1, max_length=160)


class ThreadResumeInput(ThreadReadInput):
    expected_checkpoint_digest: str | None = Field(default=None, min_length=64, max_length=64)


class ThreadSteerInput(ThreadReadInput):
    instruction: str = Field(min_length=1, max_length=20_000)
    actor_id: str | None = Field(default=None, min_length=1, max_length=160)


class ThreadForkInput(ThreadReadInput):
    child_thread_id: str | None = Field(default=None, max_length=160)
    display_name: str | None = Field(default=None, max_length=500)


class ThreadMetadataUpdateInput(ThreadReadInput):
    expected_revision: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=500)


class ThreadCommandHandlers:
    def __init__(
        self,
        *,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
        runtime: ThreadRuntimeStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        investigation_service: InvestigationService | None = None,
        investigations: InvestigationStorePort | None = None,
        decision_objects: DecisionObjectService | None = None,
    ) -> None:
        self._projects = projects
        self._threads = threads
        self._runtime = runtime
        self._ledger = ledger
        self._clock = clock
        self._ids = ids
        self._investigation_service = investigation_service
        self._investigations = investigations
        self._decision_objects = decision_objects

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        del method
        project_id = value.get("project_id")
        thread_id = value.get("thread_id")
        if not isinstance(project_id, str) or not isinstance(thread_id, str):
            return
        thread = self._threads.read(thread_id)
        if thread is not None and thread.project_id == project_id:
            self._require_authenticated_scope(thread)

    async def start(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadStartInput.model_validate(value)
            project = self._projects.read(request.project_id)
            if project is None:
                raise RpcApplicationError(
                    RpcErrorCode.PROJECT_NOT_FOUND,
                    "project was not found",
                    data={"project_id": request.project_id},
                )
            if project.lifecycle in {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "closing or archived project cannot start a thread",
                )
            now = self._clock.now()
            object_id = self._ids.new("object")
            thread = WorkThread(
                thread_id=request.thread_id or self._ids.new("thread"),
                project_id=request.project_id,
                cycle_id=request.cycle_id or self._ids.new("cycle"),
                display_name=request.display_name or request.problem[:120],
                problem=request.problem,
                scope=request.scope,
                current_object_ids=(object_id,),
                working_head_digest=head_set_digest(self._ledger.read_heads(request.project_id)),
                created_at=now,
                updated_at=now,
            )
            try:
                self._threads.create(thread)
            except ThreadAlreadyExistsError as exc:
                raise RpcApplicationError(
                    RpcErrorCode.THREAD_ALREADY_EXISTS,
                    "thread already exists",
                    data={"thread_id": thread.thread_id},
                ) from exc
            if self._decision_objects is not None:
                authenticated = current_authenticated_actor()
                try:
                    _candidate, materialized, _commit = self._decision_objects.materialize(
                        project_id=request.project_id,
                        thread_id=thread.thread_id,
                        purpose_statement=request.problem,
                        problem_frame=request.problem,
                        focus_refs=(
                            tuple(f"{key}:{child}" for key, child in sorted(request.scope.items()))
                            or (f"THREAD:{thread.thread_id}",)
                        ),
                        trigger_evidence_refs=(),
                        profile_refs=("GENERAL_RND_DECISION",),
                        actor_ref=(
                            "agent:thread-materializer"
                            if authenticated is None
                            else authenticated.actor_id
                        ),
                        object_id=object_id,
                        trigger_type="EXPLICIT_USER_REQUEST",
                        workstream_refs=tuple(request.scope.values()),
                    )
                except ValueError as exc:
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        f"thread Object materialization failed: {exc}",
                    ) from exc
                if materialized is None:
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        "thread Object materialization was held",
                    )
                thread = thread.model_copy(
                    update={
                        "working_head_digest": head_set_digest(
                            self._ledger.read_heads(request.project_id)
                        ),
                        "updated_at": self._clock.now(),
                    }
                )
                if not self._threads.update(thread, expected_revision=thread.revision):
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        "thread changed during initial Object materialization",
                    )
            authenticated = current_authenticated_actor()
            self._activity(
                thread,
                "thread/started",
                {"problem": thread.problem},
                actor_id=(
                    "system:thread-handler" if authenticated is None else authenticated.actor_id
                ),
            )
            if self._investigation_service is not None:
                self._investigation_service.start(
                    thread=thread,
                    trigger="INITIAL_PROBLEM",
                    question=thread.problem,
                    target_object_id=thread.current_object_ids[0],
                    mode="BOUNDED",
                    scope=thread.scope,
                    required_evidence_groups=("controlling plan", "current result"),
                    query_families=("project sources", "counter-search"),
                    stop_conditions=("SUFFICIENT", "DIMINISHING_INFORMATION_VALUE"),
                )
            return self._serialize(thread)

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectScopedInput.model_validate(value)
        return {
            "threads": [
                self._serialize(thread)
                for thread in self._threads.list(request.project_id)
                if authenticated_data_scope_allows(thread.scope)
            ]
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ThreadReadInput.model_validate(value)
        return self._serialize(self._read_scoped(request))

    async def activity_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ThreadReadInput.model_validate(value)
        self._read_scoped(request)
        return {
            "activities": [
                item.model_dump(mode="json")
                for item in self._runtime.list_activity(request.project_id, request.thread_id)
            ]
        }

    async def checkpoint_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ThreadReadInput.model_validate(value)
        self._read_scoped(request)
        return {
            "checkpoints": [
                item.model_dump(mode="json")
                for item in self._runtime.list_checkpoints(request.project_id, request.thread_id)
            ]
        }

    async def checkpoint_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ThreadCheckpointReadInput.model_validate(value)
        self._read_scoped(request)
        checkpoint = self._runtime.read_checkpoint(
            request.project_id, request.thread_id, request.checkpoint_id
        )
        if checkpoint is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "thread checkpoint was not found"
            )
        return {"checkpoint": checkpoint.model_dump(mode="json")}

    async def steer(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadSteerInput.model_validate(value)
            thread = self._read_scoped(request)
            if thread.lifecycle != ThreadLifecycle.OPEN:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "terminal thread cannot accept steering"
                )
            authenticated = current_authenticated_actor()
            actor_id = bind_authenticated_actor(
                request.project_id,
                request.actor_id
                or ("human:local-user" if authenticated is None else authenticated.actor_id),
            )
            record = ThreadInputRecord(
                input_id=self._ids.new("thread-input"),
                project_id=thread.project_id,
                thread_id=thread.thread_id,
                kind="STEER",
                text=request.instruction,
                ordinal=self._runtime.next_input_ordinal(thread.thread_id),
                created_at=self._clock.now(),
            )
            self._runtime.enqueue_input(record)
            self._activity(
                thread,
                "thread/steered",
                {"input_id": record.input_id, "actor_id": actor_id},
                actor_id=actor_id,
            )
            return {"queued_input": record.model_dump(mode="json")}

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadReadInput.model_validate(value)
            thread = self._read_scoped(request)
            target = (
                ThreadExecutionState.PAUSE_PENDING
                if thread.execution_state == ThreadExecutionState.RUNNING
                else ThreadExecutionState.PAUSED
            )
            updated = thread.model_copy(
                update={
                    "execution_state": target,
                    "revision": thread.revision + 1,
                    "updated_at": self._clock.now(),
                }
            )
            self._update(updated, thread.revision)
            checkpoint = self._checkpoint(updated, "pause requested")
            self._activity(
                updated,
                "thread/pauseRequested"
                if target == ThreadExecutionState.PAUSE_PENDING
                else "thread/paused",
                {"checkpoint_id": checkpoint.checkpoint_id},
            )
            return {
                **self._serialize(updated),
                "checkpoint": checkpoint.model_dump(mode="json"),
            }

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadResumeInput.model_validate(value)
            thread = self._read_scoped(request)
            checkpoints = self._runtime.list_checkpoints(request.project_id, request.thread_id)
            latest = checkpoints[-1] if checkpoints else None
            if request.expected_checkpoint_digest is not None and (
                latest is None or latest.checkpoint_digest != request.expected_checkpoint_digest
            ):
                raise RpcApplicationError(
                    RpcErrorCode.STALE_CHECKPOINT,
                    "thread resume rejected because checkpoint is missing or stale",
                )
            updated = thread.model_copy(
                update={
                    "execution_state": ThreadExecutionState.IDLE,
                    "revision": thread.revision + 1,
                    "updated_at": self._clock.now(),
                }
            )
            self._update(updated, thread.revision)
            self._activity(updated, "thread/resumed", {})
            return self._serialize(updated)

    async def stop(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadReadInput.model_validate(value)
            thread = self._read_scoped(request)
            updated = thread.model_copy(
                update={
                    "lifecycle": ThreadLifecycle.STOPPED,
                    "execution_state": ThreadExecutionState.IDLE,
                    "revision": thread.revision + 1,
                    "updated_at": self._clock.now(),
                }
            )
            self._update(updated, thread.revision)
            checkpoint = self._checkpoint(updated, "thread stopped")
            self._activity(updated, "thread/stopped", {"checkpoint_id": checkpoint.checkpoint_id})
            if self._investigation_service is not None and self._investigations is not None:
                for investigation in self._investigations.list(
                    updated.project_id, updated.thread_id
                ):
                    if investigation.domain_state not in {
                        "CONVERGED",
                        "HOLD",
                        "ABSTAINED",
                        "STOPPED",
                    }:
                        self._investigation_service.stop(
                            investigation, reason="PARENT_THREAD_STOPPED"
                        )
            return self._serialize(updated)

    async def fork(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadForkInput.model_validate(value)
            parent = self._read_scoped(request)
            if parent.execution_state == ThreadExecutionState.RUNNING:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "running thread must checkpoint before fork"
                )
            now = self._clock.now()
            child = WorkThread(
                thread_id=request.child_thread_id or self._ids.new("thread"),
                project_id=parent.project_id,
                cycle_id=self._ids.new("cycle"),
                display_name=request.display_name or f"Fork of {parent.display_name}",
                problem=parent.problem,
                scope=parent.scope,
                parent_thread_id=parent.thread_id,
                fork_origin=parent.working_head_digest,
                current_object_ids=parent.current_object_ids,
                working_head_digest=parent.working_head_digest,
                created_at=now,
                updated_at=now,
            )
            try:
                self._threads.create(child)
            except ThreadAlreadyExistsError as exc:
                raise RpcApplicationError(
                    RpcErrorCode.THREAD_ALREADY_EXISTS, "child thread already exists"
                ) from exc
            self._activity(
                child,
                "thread/forked",
                {"parent_thread_id": parent.thread_id, "fork_origin": parent.working_head_digest},
            )
            return {"thread": self._serialize(child)}

    async def metadata_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ThreadMetadataUpdateInput.model_validate(value)
            thread = self._read_scoped(request)
            updated = thread.model_copy(
                update={
                    "display_name": request.display_name,
                    "revision": thread.revision + 1,
                    "updated_at": self._clock.now(),
                }
            )
            self._update(updated, request.expected_revision)
            self._activity(
                updated, "thread/metadataUpdated", {"display_name": request.display_name}
            )
            return self._serialize(updated)

    def _read_scoped(self, request: ThreadReadInput) -> WorkThread:
        thread = self._threads.read(request.thread_id)
        if thread is None or thread.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.THREAD_NOT_FOUND,
                "thread was not found in this project",
                data={"thread_id": request.thread_id},
            )
        self._require_authenticated_scope(thread)
        return thread

    @staticmethod
    def _require_authenticated_scope(thread: WorkThread) -> None:
        if not authenticated_data_scope_allows(thread.scope):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "stored Thread workstream is outside the authenticated data scope",
                data={"reason_code": "AUTH_DATA_SCOPE_DENIED", "pre_io": True},
            )

    def _update(self, thread: WorkThread, expected_revision: int) -> None:
        if not self._threads.update(thread, expected_revision=expected_revision):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "thread revision changed concurrently",
            )

    def _checkpoint(self, thread: WorkThread, reason: str) -> ThreadCheckpoint:
        payload: dict[str, object] = {
            "thread_revision": thread.revision,
            "execution_state": thread.execution_state.value,
            "lifecycle": thread.lifecycle.value,
            "working_head_digest": thread.working_head_digest,
            "reason": reason,
        }
        draft = {
            "project_id": thread.project_id,
            "thread_id": thread.thread_id,
            "cycle_id": thread.cycle_id,
            "head_set_digest": thread.working_head_digest,
            "payload": payload,
        }
        checkpoint = ThreadCheckpoint(
            checkpoint_id=self._ids.new("thread-checkpoint"),
            project_id=thread.project_id,
            thread_id=thread.thread_id,
            cycle_id=thread.cycle_id,
            head_set_digest=thread.working_head_digest,
            payload=payload,
            checkpoint_digest=domain_digest("THREAD_CHECKPOINT", "1.0.0", canonical_payload(draft)),
            created_at=self._clock.now(),
        )
        self._runtime.put_checkpoint(checkpoint)
        return checkpoint

    def _activity(
        self,
        thread: WorkThread,
        event_type: str,
        payload: dict[str, object],
        *,
        actor_id: str = "system:thread-handler",
    ) -> ThreadActivity:
        draft = {
            "project_id": thread.project_id,
            "thread_id": thread.thread_id,
            "cycle_id": thread.cycle_id,
            "event_type": event_type,
            "payload": payload,
            "actor_id": actor_id,
            "thread_revision": thread.revision,
        }
        activity = ThreadActivity(
            activity_id=self._ids.new("thread-activity"),
            project_id=thread.project_id,
            thread_id=thread.thread_id,
            cycle_id=thread.cycle_id,
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
            activity_digest=domain_digest("THREAD_ACTIVITY", "1.0.0", canonical_payload(draft)),
            created_at=self._clock.now(),
        )
        self._runtime.append_activity(activity)
        return activity

    @staticmethod
    def _serialize(thread: WorkThread) -> dict[str, JsonValue]:
        return {str(key): child for key, child in thread.model_dump(mode="json").items()}
