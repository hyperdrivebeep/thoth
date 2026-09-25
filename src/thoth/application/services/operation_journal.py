from __future__ import annotations

from pydantic import JsonValue

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.event import Checkpoint, EventRecord
from thoth.ports.event_store import EventStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class OperationJournal:
    def __init__(
        self, store: EventStorePort, clock: ClockPort, ids: IdGeneratorPort
    ) -> None:
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
        event = EventRecord.model_validate(
            {
                **draft,
                "event_digest": domain_digest(
                    "OPERATION_EVENT", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        self._store.insert_event(event)
        return event

    def checkpoint(
        self, *, operation_id: str, payload: dict[str, JsonValue]
    ) -> Checkpoint:
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
