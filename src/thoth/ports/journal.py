from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from thoth.domain.event import Checkpoint, EventRecord


class JournalPort(Protocol):
    def append(
        self,
        *,
        project_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> EventRecord: ...

    def checkpoint(
        self, *, operation_id: str, payload: dict[str, JsonValue]
    ) -> Checkpoint: ...
