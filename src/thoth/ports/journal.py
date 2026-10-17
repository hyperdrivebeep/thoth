from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from thoth.domain.event import Checkpoint, EventRecord
from thoth.domain.operation import OperationRecord


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
