from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

import orjson
from pydantic import JsonValue, ValidationError

from thoth.adapters.storage.operations import IdempotencyConflict
from thoth.application.services.historical_access_verification import historical_query_verification
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.deployment_mode import STALE_RUNNING_MESSAGE, STALE_RUNNING_REASON
from thoth.domain.enums import OperationState
from thoth.domain.operation import (
    InternalFailureDiagnostic,
    InternalFailureStateSnapshot,
    InternalFailureTopFrame,
    OperationRecord,
)
from thoth.domain.research_execution import ResearchFence
from thoth.domain.resource_scope import (
    ResourceScopeError,
    current_resource_uses,
    resource_use_scope,
)
from thoth.ports.field_measurement import FieldMeasurementObserverPort
from thoth.ports.journal import JournalPort
from thoth.ports.model import ModelResolutionError
from thoth.ports.operation import (
    InternalFailureDiagnosticPort,
    OperationClaimBusy,
    OperationReentryDenied,
    OperationStorePort,
)
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.deferred import AcceptedRunning, PendingExecution, current_operation
from thoth.protocol.jsonrpc import (
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    RpcApplicationError,
    RpcErrorCode,
)
from thoth.protocol.notifications import notifications_for
from thoth.protocol.registry import CommandHandler, MethodRegistry

READ_QUERY_METHODS = frozenset(
    {
        "revision/timeline/read",
        "revision/restore/preview",
        "revision/diff/read",
        "revision/history/read",
        "revision/read",
        "revision/content/read",
        "revision/head/read",
        "revision/timeline/item/read",
        "thread/result/read",
        "thread/result/compare/read",
        "thread/read",
        "thread/list",
        "thread/activity/list",
        "model/settings/read",
        "model/credential/list",
        "workspace/setup/read",
        "workspace/ready",
        "project/read",
        "project/list",
        "project/review/list",
        "project/source/list",
        "evidence/list",
        "operation/read",
        "operation/result/read",
        "operation/checkpoint/read",
    }
)
RUNNING_RESEARCH_METHODS = frozenset({"thread/start", "thread/input", "thread/steer"})


@dataclass(frozen=True)
class DispatchTicket:
    request: JsonRpcRequest
    operation: OperationRecord
    handler: CommandHandler
    check_reentry: bool = True


@runtime_checkable
class PreClaimAuthorization(Protocol):
    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None: ...


class CommandBus:
    def __init__(
        self,
        registry: MethodRegistry,
        operations: OperationStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        journal: JournalPort | None = None,
        measurement: FieldMeasurementObserverPort | None = None,
        resource_access: ResourceAccessPort | None = None,
        seal_abandoned_running: bool = False,
        before_continue: Callable[[str], Awaitable[None]] | None = None,
        queued_admission: Callable[[OperationRecord], dict[str, JsonValue] | None] | None = None,
    ) -> None:
        self._registry = registry
        self._operations = operations
        self._clock = clock
        self._ids = ids
        self._journal = journal
        self._measurement = measurement
        self._resource_access = resource_access
        self._seal_abandoned_running = seal_abandoned_running
        self._before_continue = before_continue
        self._queued_admission = queued_admission
        self._operation_task_canceller: Callable[[str], None] | None = None
        self._failure_diagnostics = (
            operations if isinstance(operations, InternalFailureDiagnosticPort) else None
        )
        self._research_tasks: dict[str, asyncio.Task[JsonRpcResponse]] = {}

    async def drain(self) -> None:
        while pending := tuple(task for task in self._research_tasks.values() if not task.done()):
            await asyncio.gather(*pending, return_exceptions=True)
        for identifier, task in tuple(self._research_tasks.items()):
            if task.done():
                self._research_tasks.pop(identifier, None)

    def has_running_research_tasks(self) -> bool:
        return any(not task.done() for task in self._research_tasks.values())

    def close_tasks(self) -> None:
        for task in tuple(self._research_tasks.values()):
            task.cancel()

    def registered_methods(self) -> tuple[str, ...]:
        return self._registry.registered_methods()

    async def query(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Authorized read without creating an operation, receipt, or journal event."""
        try:
            if request.method not in READ_QUERY_METHODS:
                raise RpcApplicationError(
                    RpcErrorCode.METHOD_NOT_FOUND, "method is not a read query"
                )
            handler = self._registry.resolve(request.method)
            owner = getattr(handler, "__self__", None)
            with historical_query_verification(), resource_use_scope():
                if isinstance(owner, PreClaimAuthorization):
                    owner.authorize_before_claim(request.method, request.params.input)
                value = await handler(request.params.input)
            if isinstance(value, (AcceptedRunning, PendingExecution)):
                raise ValueError("READ_QUERY_RETURNED_EXECUTION")
            return JsonRpcResponse(
                id=request.id, result={"operation_id": "", "state": "SUCCEEDED", "value": value}
            )
        except ModelResolutionError as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=RpcErrorCode.DOMAIN_REJECTED, message=str(exc)),
            )
        except (RpcApplicationError, ResourceScopeError) as exc:
            return JsonRpcResponse(id=request.id, error=self._application_error(exc))
        except ValidationError:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=RpcErrorCode.INVALID_PARAMS, message="invalid read query parameters"
                ),
            )

    def read_operation(self, operation_id: str) -> OperationRecord | None:
        return self._operations.read(operation_id)

    def fail_if_running(
        self, operation_id: str, error: dict[str, JsonValue]
    ) -> OperationRecord | None:
        current = self._operations.read(operation_id)
        if current is None or current.state not in {
            OperationState.PENDING,
            OperationState.RUNNING,
        }:
            return current
        failed = self._operations.fail(operation_id, error, completed_at=self._clock.now())
        self._append_event(
            failed,
            "operation.failed",
            {
                "code": error.get("code", 0),
                "message": error.get("message", ""),
            },
        )
        return failed

    def bind_operation_task_canceller(self, canceller: Callable[[str], None]) -> None:
        self._operation_task_canceller = canceller

    async def dispatch(self, request: JsonRpcRequest) -> JsonRpcResponse:
        claimed = self.claim(request)
        if isinstance(claimed, JsonRpcResponse):
            return claimed
        return await self.execute(claimed)

    def claim(self, request: JsonRpcRequest) -> DispatchTicket | JsonRpcResponse:
        try:
            handler = self._registry.resolve(request.method)
            owner = getattr(handler, "__self__", None)
            if isinstance(owner, PreClaimAuthorization):
                owner.authorize_before_claim(request.method, request.params.input)
            project_id = self._project_scope(request)
            scope_digest = self._scope_digest(request)
            authenticated = current_authenticated_actor()
            candidate = OperationRecord(
                operation_id=self._ids.new("operation"),
                project_id=project_id,
                method=request.method,
                idempotency_key=request.params.meta.idempotency_key,
                scope_digest=scope_digest,
                owner_actor_id=None if authenticated is None else authenticated.actor_id,
                owner_session_id=None if authenticated is None else authenticated.session_id,
                owner_role_assignment_id=(
                    None if authenticated is None else authenticated.role_assignment_id
                ),
                owner_data_scopes=(
                    () if authenticated is None else tuple(sorted(authenticated.data_scopes))
                ),
                state=OperationState.RUNNING,
                created_at=self._clock.now(),
            )
            operation, replayed = self._operations.claim(candidate)
            if replayed:
                if operation.state == OperationState.RUNNING:
                    self._registry.authorize_reentry(operation)
                    live_task = operation.operation_id in self._research_tasks
                    queued_value = (
                        None
                        if self._queued_admission is None
                        else self._queued_admission(operation)
                    )
                    if queued_value is not None:
                        if not live_task:
                            return DispatchTicket(
                                request=request, operation=operation, handler=handler
                            )
                        return JsonRpcResponse(
                            id=request.id,
                            result={
                                "operation_id": operation.operation_id,
                                "state": "RUNNING",
                                "value": queued_value,
                            },
                        )
                    if (
                        self._seal_abandoned_running
                        and request.method in RUNNING_RESEARCH_METHODS
                        and not live_task
                    ):
                        return self._fail_abandoned_running(request, operation)
                # A claim without durable T1 is not admission. The same payload may
                # re-enter admission, but never dispatch a second admitted attempt.
                owner = getattr(handler, "__self__", None)
                admitted = getattr(owner, "admission", None)
                if operation.state == OperationState.RUNNING and callable(admitted):
                    value = cast(dict[str, JsonValue] | None, admitted(operation))
                    live_task = operation.operation_id in self._research_tasks
                    if value is None or not live_task:
                        return DispatchTicket(request=request, operation=operation, handler=handler)
                    return JsonRpcResponse(
                        id=request.id,
                        result={
                            "operation_id": operation.operation_id,
                            "state": "RUNNING",
                            "value": value,
                        },
                    )
                return self._replay(request.id, operation)
            self._append_event(operation, "operation.started", {"method": request.method})
            return DispatchTicket(request=request, operation=operation, handler=handler)
        except OperationReentryDenied as exc:
            return self._reentry_denied(request, exc)
        except OperationClaimBusy:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=RpcErrorCode.DOMAIN_REJECTED,
                    message="operation storage is busy; retry the same request later",
                    data={
                        "reason_code": "OPERATION_CLAIM_BUSY",
                        "operation_claimed": False,
                        "retryable": True,
                    },
                ),
            )
        except IdempotencyConflict as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=RpcErrorCode.IDEMPOTENCY_CONFLICT,
                    message="idempotency key conflicts with a different request",
                    data={"existingOperationId": exc.existing.operation_id},
                ),
            )
        except (RpcApplicationError, ResourceScopeError) as exc:
            return JsonRpcResponse(id=request.id, error=self._application_error(exc))

    async def execute(self, ticket: DispatchTicket) -> JsonRpcResponse:
        if self._resource_access is None:
            return await self._execute(ticket)
        with resource_use_scope(ticket.operation.project_id):
            return await self._execute(ticket)

    async def _execute(self, ticket: DispatchTicket) -> JsonRpcResponse:
        request = ticket.request
        operation = ticket.operation
        pre_failure_state = self._capture_failure_state(operation, request)
        try:
            if ticket.check_reentry:
                self._registry.authorize_reentry(operation)
            if self._measurement is not None and request.params.meta.field_session_id is not None:
                try:
                    self._measurement.validate_session(
                        project_id=operation.project_id,
                        session_id=request.params.meta.field_session_id,
                    )
                except ValueError as exc:
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        str(exc),
                    ) from exc
            token = current_operation.set(operation)
            try:
                value = await ticket.handler(request.params.input)
            finally:
                current_operation.reset(token)
            sealed = self._operations.read(operation.operation_id)
            if sealed is not None and sealed.state in {
                OperationState.SUCCEEDED,
                OperationState.FAILED,
                OperationState.CANCELLED,
            }:
                # A use-case terminal UoW owns the result/error and binding already.
                try:
                    return self._replay(request.id, sealed)
                except (RpcApplicationError, ResourceScopeError) as exc:
                    return JsonRpcResponse(id=request.id, error=self._application_error(exc))
            if isinstance(value, PendingExecution):
                current = self._operations.read(operation.operation_id) or operation
                return JsonRpcResponse(
                    id=request.id,
                    result={
                        "operation_id": operation.operation_id,
                        "state": current.state.value,
                        "value": value.value,
                    },
                )
            if isinstance(value, AcceptedRunning):
                return self._continue_accepted(request, operation, value)
            if self._resource_access is not None:
                self._resource_access.require_operation(
                    operation.model_copy(
                        update={
                            "result": value,
                            "resource_uses": current_resource_uses(),
                        }
                    )
                )
            if request.method == "operation/cancel" and value.get("cancelled") is True:
                target = request.params.input.get("operation_id")
                if isinstance(target, str) and self._operation_task_canceller is not None:
                    self._operation_task_canceller(target)
                if isinstance(target, str) and target in self._research_tasks:
                    self._research_tasks[target].cancel()
            cancel_target = value.get("cancel_operation_id")
            if isinstance(cancel_target, str) and cancel_target in self._research_tasks:
                self._research_tasks[cancel_target].cancel()
        except OperationReentryDenied as exc:
            return self._reentry_denied(request, exc)
        except asyncio.CancelledError:
            self._operations.cancel(operation.operation_id, completed_at=self._clock.now())
            raise
        except ResearchFence as exc:
            self._operations.cancel(operation.operation_id, completed_at=self._clock.now())
            return JsonRpcResponse(
                id=request.id,
                result={
                    "operation_id": operation.operation_id,
                    "state": "CANCELLED",
                    "value": {"reason": str(exc) or "CANCELLED"},
                },
            )
        except (RpcApplicationError, ResourceScopeError) as exc:
            error = self._application_error(exc)
            self._operations.fail(
                operation.operation_id,
                error.model_dump(mode="json"),
                completed_at=self._clock.now(),
            )
            self._append_event(
                operation,
                "operation.failed",
                {"code": error.code, "message": error.message},
            )
            self._measure(request, operation.project_id, "FAILED")
            return JsonRpcResponse(id=request.id, error=error)
        except PermissionError as exc:
            error = JsonRpcError(
                code=RpcErrorCode.AUTHORIZATION_DENIED,
                message="authenticated authority identity mismatch",
                data={"reason_code": str(exc), "pre_io": True},
            )
            self._operations.fail(
                operation.operation_id,
                error.model_dump(mode="json"),
                completed_at=self._clock.now(),
            )
            self._append_event(
                operation,
                "operation.failed",
                {"code": error.code, "message": error.message},
            )
            self._measure(request, operation.project_id, "FAILED")
            return JsonRpcResponse(id=request.id, error=error)
        except ValidationError as exc:
            error = JsonRpcError(
                code=RpcErrorCode.INVALID_PARAMS,
                message="invalid method parameters",
                data={
                    "validation": cast(
                        JsonValue,
                        orjson.loads(exc.json(include_url=False)),
                    )
                },
            )
            self._operations.fail(
                operation.operation_id,
                error.model_dump(mode="json"),
                completed_at=self._clock.now(),
            )
            self._append_event(
                operation,
                "operation.failed",
                {"code": error.code, "message": error.message},
            )
            self._measure(request, operation.project_id, "FAILED")
            return JsonRpcResponse(id=request.id, error=error)
        except Exception as exc:
            self._persist_internal_failure(
                operation=operation,
                request=request,
                exc=exc,
                pre_state=pre_failure_state,
            )
            error = JsonRpcError(
                code=RpcErrorCode.INTERNAL_ERROR,
                message="internal command failure",
                data={"operation_id": operation.operation_id},
            )
            self._operations.fail(
                operation.operation_id,
                error.model_dump(mode="json"),
                completed_at=self._clock.now(),
            )
            self._append_event(
                operation,
                "operation.failed",
                {"code": error.code, "message": error.message},
            )
            self._measure(request, operation.project_id, "FAILED")
            return JsonRpcResponse(id=request.id, error=error)
        completed = self._operations.complete(
            operation.operation_id,
            value,
            completed_at=self._clock.now(),
        )
        result_digest = domain_digest("OPERATION_RESULT", "1.0.0", canonical_payload(value))
        if self._journal is not None:
            self._journal.checkpoint(
                operation_id=operation.operation_id,
                payload={"state": completed.state.value, "result_digest": result_digest},
            )
        for notification in notifications_for(request.method, value):
            self._append_event(
                operation,
                notification,
                {"method": request.method, "result_digest": result_digest},
            )
        self._append_event(
            operation,
            "operation.succeeded",
            {"result_digest": result_digest},
        )
        self._measure(request, operation.project_id, "SUCCEEDED")
        return JsonRpcResponse(id=request.id, result=self._result_payload(completed))

    def _measure(self, request: JsonRpcRequest, project_id: str, outcome: str) -> None:
        session_id = request.params.meta.field_session_id
        if self._measurement is None or session_id is None:
            return
        try:
            self._measurement.record_rpc_event(
                project_id=project_id,
                session_id=session_id,
                method=request.method,
                outcome=outcome,
            )
        except ValueError:
            return

    def _project_scope(self, request: JsonRpcRequest) -> str:
        if request.method in {
            "workspace/setup/read",
            "workspace/setup/update",
            "workspace/ready",
            "model/credential/list",
            "model/credential/register",
        }:
            return "system:workspace"
        value = request.params.input.get("project_id")
        if not isinstance(value, str) or not value:
            raise RpcApplicationError(
                RpcErrorCode.INVALID_PARAMS,
                "input.project_id is required for project isolation",
            )
        return value

    def _continue_accepted(
        self, request: JsonRpcRequest, operation: OperationRecord, value: AcceptedRunning
    ) -> JsonRpcResponse:
        target_operation = value.target_operation or operation
        running_task = self._research_tasks.get(target_operation.operation_id)
        if running_task is not None and not running_task.done():
            if target_operation.operation_id != operation.operation_id:
                completed = self._operations.complete(
                    operation.operation_id, value.value, completed_at=self._clock.now()
                )
                return JsonRpcResponse(id=request.id, result=self._result_payload(completed))
            return JsonRpcResponse(
                id=request.id,
                result={
                    "operation_id": operation.operation_id,
                    "state": "RUNNING",
                    "value": value.value,
                },
            )

        async def continuation(
            _: dict[str, JsonValue],
        ) -> dict[str, JsonValue] | PendingExecution:
            if self._before_continue is not None:
                await self._before_continue(target_operation.operation_id)
            return await value.continue_execution()

        task = asyncio.create_task(
            self.execute(
                DispatchTicket(
                    request=request,
                    operation=target_operation,
                    handler=continuation,
                    check_reentry=False,
                )
            )
        )
        self._research_tasks[target_operation.operation_id] = task

        def remove_finished(finished: asyncio.Task[JsonRpcResponse]) -> None:
            if self._research_tasks.get(target_operation.operation_id) is finished:
                self._research_tasks.pop(target_operation.operation_id, None)

        task.add_done_callback(remove_finished)
        if target_operation.operation_id != operation.operation_id:
            completed = self._operations.complete(
                operation.operation_id, value.value, completed_at=self._clock.now()
            )
            return JsonRpcResponse(id=request.id, result=self._result_payload(completed))
        return JsonRpcResponse(
            id=request.id,
            result={
                "operation_id": operation.operation_id,
                "state": "RUNNING",
                "value": value.value,
            },
        )

    def _scope_digest(self, request: JsonRpcRequest) -> str:
        payload = {
            "method": request.method,
            "input": request.params.input,
            "expected_head_digest": request.params.meta.expected_head_digest,
            "field_session_id": request.params.meta.field_session_id,
        }
        return domain_digest("RPC_SCOPE", "1.0.0", canonical_payload(payload))

    def _fail_abandoned_running(
        self, request: JsonRpcRequest, operation: OperationRecord
    ) -> JsonRpcResponse:
        error = JsonRpcError(
            code=RpcErrorCode.DOMAIN_REJECTED,
            message=STALE_RUNNING_MESSAGE,
            data={
                "reason_code": STALE_RUNNING_REASON,
                "remote_observation": "UNKNOWN",
            },
        )
        failed = self._operations.fail(
            operation.operation_id,
            error.model_dump(mode="json"),
            completed_at=self._clock.now(),
        )
        self._append_event(
            failed,
            "operation.failed",
            {"code": error.code, "message": error.message},
        )
        self._measure(request, failed.project_id, "FAILED")
        return JsonRpcResponse(id=request.id, error=error)

    def _replay(self, request_id: str | int, operation: OperationRecord) -> JsonRpcResponse:
        if self._resource_access is not None:
            self._resource_access.require_operation(operation)
        if operation.state == OperationState.SUCCEEDED and operation.result is not None:
            return JsonRpcResponse(id=request_id, result=self._result_payload(operation))
        if operation.state == OperationState.FAILED and operation.error is not None:
            return JsonRpcResponse(
                id=request_id,
                error=JsonRpcError.model_validate(operation.error),
            )
        return JsonRpcResponse(
            id=request_id,
            result={
                "operation_id": operation.operation_id,
                "state": operation.state.value,
                "value": None,
                "execution_observation": "UNKNOWN",
            },
        )

    @staticmethod
    def _result_payload(operation: OperationRecord) -> dict[str, JsonValue]:
        return {
            "operation_id": operation.operation_id,
            "state": operation.state.value,
            "value": cast(JsonValue, operation.result),
        }

    @staticmethod
    def _reentry_denied(request: JsonRpcRequest, exc: OperationReentryDenied) -> JsonRpcResponse:
        return JsonRpcResponse(
            id=request.id,
            error=JsonRpcError(
                code=RpcErrorCode.AUTHORIZATION_DENIED,
                message=str(exc),
                data={"original_operation_unchanged": True},
            ),
        )

    @staticmethod
    def _application_error(exc: RpcApplicationError | ResourceScopeError) -> JsonRpcError:
        if isinstance(exc, ResourceScopeError):
            return JsonRpcError(
                code=RpcErrorCode.AUTHORIZATION_DENIED,
                message="resource scope or access was rejected",
                data={"reason_code": exc.code},
            )
        return JsonRpcError(code=exc.code, message=exc.message, data=exc.data)

    def _append_event(
        self,
        operation: OperationRecord,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        if self._journal is None:
            return
        self._journal.append(
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            event_type=event_type,
            payload=payload,
        )

    def _capture_failure_state(
        self, operation: OperationRecord, request: JsonRpcRequest
    ) -> InternalFailureStateSnapshot | None:
        if self._failure_diagnostics is None or request.method != "thread/input":
            return None
        try:
            return self._failure_diagnostics.capture_internal_failure_state(
                project_id=operation.project_id
            )
        except Exception:
            return None

    def _persist_internal_failure(
        self,
        *,
        operation: OperationRecord,
        request: JsonRpcRequest,
        exc: Exception,
        pre_state: InternalFailureStateSnapshot | None,
    ) -> None:
        diagnostics = self._failure_diagnostics
        thread_id = request.params.input.get("thread_id")
        if (
            diagnostics is None
            or request.method != "thread/input"
            or pre_state is None
            or not isinstance(thread_id, str)
        ):
            return
        try:
            post_state = diagnostics.capture_internal_failure_state(project_id=operation.project_id)
            input_consumed = diagnostics.is_thread_input_consumed(
                project_id=operation.project_id,
                thread_id=thread_id,
            )
            traceback = exc.__traceback__
            if traceback is None:
                return
            while traceback.tb_next is not None:
                traceback = traceback.tb_next
            code = traceback.tb_frame.f_code
            top_frame = InternalFailureTopFrame(
                file=code.co_filename.replace("\\", "/").rsplit("/", 1)[-1],
                function=code.co_name,
                line=traceback.tb_lineno,
            )
            exception_type = type(exc).__name__
            fingerprint = domain_digest(
                "INTERNAL_FAILURE",
                "1.0.0",
                canonical_payload(
                    {
                        "exception_type": exception_type,
                        "top_frame": top_frame.model_dump(mode="json"),
                    }
                ),
            )
            diagnostics.persist_internal_failure(
                InternalFailureDiagnostic(
                    exception_type=exception_type,
                    top_frame=top_frame,
                    fingerprint=fingerprint,
                    operation_id=operation.operation_id,
                    input_consumed=input_consumed,
                    pre_state=pre_state,
                    post_state=post_state,
                )
            )
        except Exception:
            return
