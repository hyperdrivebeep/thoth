from __future__ import annotations

from pydantic import JsonValue

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.event import Checkpoint, EventRecord
from thoth.domain.operation import OperationRecord
from thoth.ports.event_store import EventStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class OperationJournal:
    def __init__(self, store: EventStorePort, clock: ClockPort, ids: IdGeneratorPort) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def append(
        self,
        *,
        project_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> EventRecord:
        previous_events = self._store.list_events(operation_id)
        previous = None if not previous_events else previous_events[-1].event_digest
        event = self._prepare_event(
            project_id=project_id,
            operation_id=operation_id,
            event_type=event_type,
            payload=payload,
            previous=previous,
        )
        self._store.insert_event(event)
        return event

    def prepare_started(self, operation: OperationRecord) -> EventRecord:
        return self._prepare_event(
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            event_type="operation.started",
            payload={"method": operation.method},
            previous=None,
        )

    def _prepare_event(
        self,
        *,
        project_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
        previous: str | None,
    ) -> EventRecord:
        draft: dict[str, object] = {
            "event_id": self._ids.new("event"),
            "project_id": project_id,
            "operation_id": operation_id,
            "event_type": event_type,
            "payload": payload,
            "previous_event_digest": previous,
            "created_at": self._clock.now(),
            "schema_version": "1.0.0",
        }
        return EventRecord.model_validate(
            {
                **draft,
                "event_digest": domain_digest("OPERATION_EVENT", "1.0.0", canonical_payload(draft)),
            }
        )

    def checkpoint(self, *, operation_id: str, payload: dict[str, JsonValue]) -> Checkpoint:
        draft: dict[str, object] = {
            "checkpoint_id": self._ids.new("checkpoint"),
            "operation_id": operation_id,
            "payload": payload,
            "created_at": self._clock.now(),
            "schema_version": "1.0.0",
        }
        checkpoint = Checkpoint.model_validate(
            {
                **draft,
                "checkpoint_digest": domain_digest(
                    "OPERATION_CHECKPOINT", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        self._store.insert_checkpoint(checkpoint)
        return checkpoint
