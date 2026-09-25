from __future__ import annotations

from pydantic import Field, JsonValue, ValidationError

from thoth.application.services.historical_access_verification import (
    historical_verification_allowed,
    require_historical_operation_access,
)
from thoth.application.services.resource_scope_read_context import scope_read_transaction
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.operation import OperationRecord
from thoth.ports.event_store import EventStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import ClockPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class OperationReadInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    operation_id: str = Field(min_length=1, max_length=160)


class OperationResumeInput(OperationReadInput):
    expected_checkpoint_digest: str = Field(min_length=64, max_length=64)


class OperationPauseInput(OperationReadInput):
    expected_checkpoint_digest: str | None = Field(default=None, min_length=64, max_length=64)


class OperationCommandHandlers:
    def __init__(
        self,
        store: OperationStorePort,
        events: EventStorePort | None = None,
        clock: ClockPort | None = None,
        resource_access: ResourceAccessPort | None = None,
        read_ledger: LedgerPort | None = None,
    ) -> None:
        self._store = store
        self._events = events
        self._clock = clock
        self._resource_access = resource_access
        self._read_ledger = read_ledger

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        if current_authenticated_actor() is None:
            return
        input_model: type[OperationReadInput] = {
            "operation/pause": OperationPauseInput,
            "operation/resume": OperationResumeInput,
        }.get(method, OperationReadInput)
        try:
            request = input_model.model_validate(value)
        except ValidationError:
            # Handler validation retains the existing typed INVALID_PARAMS path.
            return
        operation = (
            self._read_query_scoped(request)
            if self._pure_read_method(method)
            else self._read_scoped(request)
        )
        if method == "operation/cancel":
            self._require_cancel_owner(operation)

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OperationReadInput.model_validate(value)
        operation = self._read_query_scoped(request)
        payload = self._serialize(operation)
        if self._events is not None:
            payload["events"] = [
                event.model_dump(mode="json")
                for event in self._events.list_events(operation.operation_id)
            ]
            checkpoint = self._events.latest_checkpoint(operation.operation_id)
            payload["latest_checkpoint"] = (
                None if checkpoint is None else checkpoint.model_dump(mode="json")
            )
        return payload

    async def result(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OperationReadInput.model_validate(value)
        operation = self._read_query_scoped(request)
        return {
            "operation_id": operation.operation_id,
            "state": operation.state.value,
            "result": operation.result,
            "error": operation.error,
            "execution_observation": "UNKNOWN",
        }

    async def checkpoint(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OperationReadInput.model_validate(value)
        operation = self._read_query_scoped(request)
        checkpoint = (
            None if self._events is None else self._events.latest_checkpoint(operation.operation_id)
        )
        if checkpoint is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "operation has no durable checkpoint",
            )
        return {"checkpoint": checkpoint.model_dump(mode="json")}

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OperationPauseInput.model_validate(value)
        operation = self._read_scoped(request)
        checkpoint = (
            None if self._events is None else self._events.latest_checkpoint(operation.operation_id)
        )
        if request.expected_checkpoint_digest is not None and (
            checkpoint is None or checkpoint.checkpoint_digest != request.expected_checkpoint_digest
        ):
            raise RpcApplicationError(
                RpcErrorCode.STALE_CHECKPOINT,
                "operation pause rejected because checkpoint is missing or stale",
            )
        return {
            "operation_id": operation.operation_id,
            "pause_accepted": False,
            "state": operation.state.value,
            "reason": "NO_METHOD_SPECIFIC_PAUSE_HANDLER",
            "checkpoint_digest": (None if checkpoint is None else checkpoint.checkpoint_digest),
        }

    async def cancel(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OperationReadInput.model_validate(value)
        operation = self._read_scoped(request)
        self._require_cancel_owner(operation)
        if self._clock is None:
            raise RpcApplicationError(
                RpcErrorCode.INTERNAL_ERROR, "operation cancellation clock is unavailable"
            )
        updated = self._store.cancel(operation.operation_id, completed_at=self._clock.now())
        return {
            "operation_id": updated.operation_id,
            "state": updated.state.value,
            "cancelled": updated.state.value == "CANCELLED",
        }

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OperationResumeInput.model_validate(value)
        operation = self._read_scoped(request)
        checkpoint = (
            None if self._events is None else self._events.latest_checkpoint(operation.operation_id)
        )
        if checkpoint is None or checkpoint.checkpoint_digest != request.expected_checkpoint_digest:
            raise RpcApplicationError(
                RpcErrorCode.STALE_CHECKPOINT,
                "operation resume rejected because checkpoint is missing or stale",
                data={"operation_id": operation.operation_id},
            )
        return {
            "operation_id": operation.operation_id,
            "resume_allowed": False,
            "reason": "NO_METHOD_SPECIFIC_RESUME_HANDLER",
            "checkpoint_digest": checkpoint.checkpoint_digest,
        }

    def _read_scoped(self, request: OperationReadInput) -> OperationRecord:
        operation = self._store.read(request.operation_id)
        if operation is None or operation.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.OPERATION_NOT_FOUND,
                "operation was not found in this project",
                data={"operation_id": request.operation_id},
            )
        self._require_access_scope(operation)
        if self._resource_access is not None:
            require_historical_operation_access(self._resource_access, operation)
        return operation

    @staticmethod
    def _pure_read_method(method: str) -> bool:
        return method in {
            "operation/read",
            "operation/result/read",
            "operation/checkpoint/read",
        }

    def _read_query_scoped(self, request: OperationReadInput) -> OperationRecord:
        if self._read_ledger is None or not historical_verification_allowed():
            return self._read_scoped(request)
        with self._read_ledger.transaction(), scope_read_transaction():
            return self._read_scoped(request)

    @staticmethod
    def _require_access_scope(operation: OperationRecord) -> None:
        authenticated = current_authenticated_actor()
        if authenticated is None:
            return
        if operation.project_id == authenticated.project_id:
            if "PROJECT" in authenticated.data_scopes:
                return
            # These are origin actor permissions, not proven resource ownership.
            # Require coverage of the entire persisted envelope; never derive a
            # narrower workstream from caller metadata or an operation result.
            scopes = operation.owner_data_scopes
            if scopes and all(
                scope.startswith("WORKSTREAM:")
                and bool(scope.removeprefix("WORKSTREAM:"))
                and scope in authenticated.data_scopes
                for scope in scopes
            ):
                return
        raise RpcApplicationError(
            RpcErrorCode.AUTHORIZATION_DENIED,
            "operation access exceeds the authenticated data scope",
            data={"reason_code": "AUTH_OPERATION_SCOPE_DENIED", "pre_io": True},
        )

    @staticmethod
    def _require_cancel_owner(operation: OperationRecord) -> None:
        authenticated = current_authenticated_actor()
        if authenticated is None:
            return
        if (
            operation.owner_actor_id != authenticated.actor_id
            or operation.owner_session_id != authenticated.session_id
            or operation.owner_role_assignment_id != authenticated.role_assignment_id
            or operation.owner_data_scopes != tuple(sorted(authenticated.data_scopes))
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "operation cancellation requires the originating authenticated session",
                data={"reason_code": "AUTH_OPERATION_OWNER_DENIED", "pre_io": True},
            )

    @staticmethod
    def _serialize(operation: OperationRecord) -> dict[str, JsonValue]:
        payload = operation.model_dump(mode="json")
        return {str(key): child for key, child in payload.items()}
