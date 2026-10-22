from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from thoth.domain.event import Checkpoint, EventRecord
from thoth.domain.operation import OperationRecord
from thoth.ports.runtime import AtomicUnitOfWorkPort


class JournalPort(Protocol):
    def prepare_started(self, operation: OperationRecord) -> EventRecord:
        """Build the first event with ID/clock/digest only; never read or write storage."""
        ...

    def append(
        self,
        *,
        project_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> EventRecord: ...

    def checkpoint(self, *, operation_id: str, payload: dict[str, JsonValue]) -> Checkpoint: ...


class JournalCompletionUnavailable(RuntimeError):
    """A repair storage failure; the existing successful result remains authoritative."""


@runtime_checkable
class JournalCompletionStorePort(Protocol):
    """Optional complete journal reads and repair-only infrastructure error boundary."""

    def list_events(self, operation_id: str) -> tuple[EventRecord, ...]: ...
    def list_checkpoints(self, operation_id: str) -> tuple[Checkpoint, ...]: ...
    def completion_transaction(self, uow: AtomicUnitOfWorkPort) -> AbstractContextManager[None]: ...
