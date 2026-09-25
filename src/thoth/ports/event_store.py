from __future__ import annotations

from typing import Protocol

from thoth.domain.event import Checkpoint, EventRecord


class EventStorePort(Protocol):
    def insert_event(self, event: EventRecord) -> None: ...

    def list_events(self, operation_id: str) -> tuple[EventRecord, ...]: ...

    def insert_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def latest_checkpoint(self, operation_id: str) -> Checkpoint | None: ...
