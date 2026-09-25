from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from thoth.domain.operation import (
    InternalFailureDiagnostic,
    InternalFailureStateSnapshot,
    OperationRecord,
)


class OperationClaimBusy(RuntimeError):
    """Claim transaction was rolled back before the command could be dispatched."""


class OperationReentryDenied(PermissionError):
    """A caller's rejected reentry must not mutate the persisted operation."""


class OperationReentryAuthorizer(Protocol):
    def __call__(self, operation: OperationRecord) -> None: ...


class OperationStorePort(Protocol):
    def claim(self, candidate: OperationRecord) -> tuple[OperationRecord, bool]: ...

    def complete(
        self, operation_id: str, result: dict[str, JsonValue], *, completed_at: datetime
    ) -> OperationRecord: ...

    def fail(
        self, operation_id: str, error: dict[str, JsonValue], *, completed_at: datetime
    ) -> OperationRecord: ...

    def read(self, operation_id: str) -> OperationRecord | None: ...

    def list_by_project(
        self,
        project_id: str,
        *,
        idempotency_prefix: str | None = None,
    ) -> tuple[OperationRecord, ...]: ...

    def cancel(self, operation_id: str, *, completed_at: datetime) -> OperationRecord: ...


@runtime_checkable
class InternalFailureDiagnosticPort(Protocol):
    def capture_internal_failure_state(
        self, *, project_id: str
    ) -> InternalFailureStateSnapshot: ...

    def is_thread_input_consumed(self, *, project_id: str, thread_id: str) -> bool: ...

    def persist_internal_failure(self, diagnostic: InternalFailureDiagnostic) -> None: ...
