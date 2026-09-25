from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, func, insert, select, update

from thoth.adapters.storage.schema import thread_activities, thread_checkpoints, thread_inputs
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.thread_runtime import ThreadActivity, ThreadCheckpoint, ThreadInputRecord
from thoth.ports.thread_runtime import ThreadRuntimeStorePort


def _object(value: str) -> dict[str, object]:
    loaded = cast(object, orjson.loads(value))
    if not isinstance(loaded, dict):
        raise ValueError("stored thread runtime payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], loaded).items()}


class SqliteThreadRuntimeStore(ThreadRuntimeStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_activity(self, value: ThreadActivity) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(thread_activities).values(
                    activity_id=value.activity_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    cycle_id=value.cycle_id,
                    event_type=value.event_type,
                    payload_json=orjson.dumps(value.payload, option=orjson.OPT_SORT_KEYS).decode(),
                    actor_id=value.actor_id,
                    activity_digest=value.activity_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_activity(self, project_id: str, thread_id: str) -> tuple[ThreadActivity, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(thread_activities)
                .where(
                    thread_activities.c.project_id == project_id,
                    thread_activities.c.thread_id == thread_id,
                )
                .order_by(thread_activities.c.created_at, thread_activities.c.activity_id)
            ).mappings()
            return tuple(
                ThreadActivity(
                    activity_id=str(row["activity_id"]),
                    project_id=str(row["project_id"]),
                    thread_id=str(row["thread_id"]),
                    cycle_id=str(row["cycle_id"]),
                    event_type=str(row["event_type"]),
                    payload=_object(str(row["payload_json"])),
                    actor_id=str(row["actor_id"]),
                    activity_digest=str(row["activity_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    def put_checkpoint(self, value: ThreadCheckpoint) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(thread_checkpoints).values(
                    checkpoint_id=value.checkpoint_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    cycle_id=value.cycle_id,
                    head_set_digest=value.head_set_digest,
                    payload_json=orjson.dumps(value.payload, option=orjson.OPT_SORT_KEYS).decode(),
                    checkpoint_digest=value.checkpoint_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_checkpoints(self, project_id: str, thread_id: str) -> tuple[ThreadCheckpoint, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(thread_checkpoints)
                .where(
                    thread_checkpoints.c.project_id == project_id,
                    thread_checkpoints.c.thread_id == thread_id,
                )
                .order_by(thread_checkpoints.c.created_at, thread_checkpoints.c.checkpoint_id)
            ).mappings()
            return tuple(self._checkpoint(row) for row in rows)

    def read_checkpoint(
        self, project_id: str, thread_id: str, checkpoint_id: str
    ) -> ThreadCheckpoint | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(thread_checkpoints).where(
                        thread_checkpoints.c.project_id == project_id,
                        thread_checkpoints.c.thread_id == thread_id,
                        thread_checkpoints.c.checkpoint_id == checkpoint_id,
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else self._checkpoint(row)

    def next_input_ordinal(self, thread_id: str) -> int:
        with read_connection(self._engine) as connection:
            current = connection.execute(
                select(func.max(thread_inputs.c.ordinal)).where(
                    thread_inputs.c.thread_id == thread_id
                )
            ).scalar_one_or_none()
        return 1 if current is None else int(current) + 1

    def enqueue_input(self, value: ThreadInputRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(thread_inputs).values(
                    input_id=value.input_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    kind=value.kind,
                    text=value.text,
                    state=value.state,
                    ordinal=value.ordinal,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_pending_inputs(self, project_id: str, thread_id: str) -> tuple[ThreadInputRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(thread_inputs)
                .where(
                    thread_inputs.c.project_id == project_id,
                    thread_inputs.c.thread_id == thread_id,
                    thread_inputs.c.state == "QUEUED",
                )
                .order_by(thread_inputs.c.ordinal)
            ).mappings()
            return tuple(
                ThreadInputRecord(
                    input_id=str(row["input_id"]),
                    project_id=str(row["project_id"]),
                    thread_id=str(row["thread_id"]),
                    kind=str(row["kind"]),
                    text=str(row["text"]),
                    state=str(row["state"]),
                    ordinal=int(row["ordinal"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    def mark_inputs_consumed(self, input_ids: tuple[str, ...]) -> None:
        if not input_ids:
            return
        with write_connection(self._engine) as connection:
            connection.execute(
                update(thread_inputs)
                .where(thread_inputs.c.input_id.in_(input_ids))
                .values(state="CONSUMED")
            )

    @staticmethod
    def _checkpoint(row: object) -> ThreadCheckpoint:
        values = cast(dict[str, object], row)
        return ThreadCheckpoint(
            checkpoint_id=str(values["checkpoint_id"]),
            project_id=str(values["project_id"]),
            thread_id=str(values["thread_id"]),
            cycle_id=str(values["cycle_id"]),
            head_set_digest=str(values["head_set_digest"]),
            payload=_object(str(values["payload_json"])),
            checkpoint_digest=str(values["checkpoint_digest"]),
            created_at=datetime.fromisoformat(str(values["created_at"])),
        )
