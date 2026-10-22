from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Connection, Engine, Integer, insert, literal_column, select
from sqlalchemy.exc import OperationalError

from thoth.adapters.storage.schema import checkpoints, events
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.event import Checkpoint, EventRecord
from thoth.ports.event_store import EventStorePort
from thoth.ports.journal import JournalCompletionUnavailable
from thoth.ports.runtime import AtomicUnitOfWorkPort


def _object(value: str) -> dict[str, object]:
    loaded = cast(object, orjson.loads(value))
    if not isinstance(loaded, dict):
        raise ValueError("stored journal payload is not an object")
    mapping = cast(dict[object, object], loaded)
    return {str(key): child for key, child in mapping.items()}


def insert_event_on_connection(connection: Connection, event: EventRecord) -> None:
    """Write the unchanged event representation inside its caller's transaction."""
    connection.execute(
        insert(events).values(
            event_id=event.event_id,
            project_id=event.project_id,
            operation_id=event.operation_id,
            event_type=event.event_type,
            payload_json=orjson.dumps(event.payload, option=orjson.OPT_SORT_KEYS).decode(),
            previous_event_digest=event.previous_event_digest,
            event_digest=event.event_digest,
            created_at=event.created_at.isoformat(),
        )
    )


class SqliteEventStore(EventStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def completion_transaction(self, uow: AtomicUnitOfWorkPort) -> Generator[None]:
        try:
            with uow.transaction():
                yield
        except OperationalError as exc:
            raise JournalCompletionUnavailable("JOURNAL_STORAGE_UNAVAILABLE") from exc

    def list_checkpoints(self, operation_id: str) -> tuple[Checkpoint, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(checkpoints)
                    .where(checkpoints.c.operation_id == operation_id)
                    .order_by(checkpoints.c.created_at, checkpoints.c.checkpoint_id)
                )
                .mappings()
                .all()
            )
        return tuple(
            Checkpoint(
                checkpoint_id=str(row["checkpoint_id"]),
                operation_id=str(row["operation_id"]),
                payload=_object(str(row["payload_json"])),
                checkpoint_digest=str(row["checkpoint_digest"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        )

    def insert_event(self, event: EventRecord) -> None:
        with write_connection(self._engine) as connection:
            insert_event_on_connection(connection, event)

    def list_events(self, operation_id: str) -> tuple[EventRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(events)
                    .where(events.c.operation_id == operation_id)
                    .order_by(events.c.created_at, events.c.event_id)
                )
                .mappings()
                .all()
            )
        return tuple(
            EventRecord(
                event_id=str(row["event_id"]),
                project_id=str(row["project_id"]),
                operation_id=str(row["operation_id"]),
                event_type=str(row["event_type"]),
                payload=_object(str(row["payload_json"])),
                previous_event_digest=(
                    None
                    if row["previous_event_digest"] is None
                    else str(row["previous_event_digest"])
                ),
                event_digest=str(row["event_digest"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        )

    def insert_checkpoint(self, checkpoint: Checkpoint) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(checkpoints).values(
                    checkpoint_id=checkpoint.checkpoint_id,
                    operation_id=checkpoint.operation_id,
                    payload_json=orjson.dumps(
                        checkpoint.payload, option=orjson.OPT_SORT_KEYS
                    ).decode(),
                    checkpoint_digest=checkpoint.checkpoint_digest,
                    created_at=checkpoint.created_at.isoformat(),
                )
            )

    def latest_checkpoint(self, operation_id: str) -> Checkpoint | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(checkpoints)
                    .where(checkpoints.c.operation_id == operation_id)
                    .order_by(literal_column("rowid", Integer()).desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Checkpoint(
            checkpoint_id=str(row["checkpoint_id"]),
            operation_id=str(row["operation_id"]),
            payload=_object(str(row["payload_json"])),
            checkpoint_digest=str(row["checkpoint_digest"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
