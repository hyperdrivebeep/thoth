from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import orjson
from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from thoth.adapters.storage.schema import threads
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.enums import ThreadExecutionState, ThreadLifecycle
from thoth.domain.project import WorkThread
from thoth.ports.thread import ThreadAlreadyExistsError, ThreadStorePort


class SqliteThreadStore(ThreadStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, thread: WorkThread) -> None:
        created_at = thread.created_at or datetime.now(UTC)
        updated_at = thread.updated_at or created_at
        try:
            with write_connection(self._engine) as connection:
                connection.execute(
                    insert(threads).values(
                        thread_id=thread.thread_id,
                        project_id=thread.project_id,
                        cycle_id=thread.cycle_id,
                        problem=thread.problem,
                        display_name=thread.display_name,
                        scope_json=orjson.dumps(thread.scope).decode(),
                        parent_thread_id=thread.parent_thread_id,
                        fork_origin=thread.fork_origin,
                        lifecycle=thread.lifecycle.value,
                        execution_state=thread.execution_state.value,
                        current_object_ids_json=orjson.dumps(thread.current_object_ids).decode(),
                        working_head_digest=thread.working_head_digest,
                        revision=thread.revision,
                        created_at=created_at.isoformat(),
                        updated_at=updated_at.isoformat(),
                    )
                )
        except IntegrityError as exc:
            raise ThreadAlreadyExistsError(thread.thread_id) from exc

    def read(self, thread_id: str) -> WorkThread | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(select(threads).where(threads.c.thread_id == thread_id))
                .mappings()
                .first()
            )
        return None if row is None else self._deserialize(row)

    def update(self, thread: WorkThread, *, expected_revision: int | None = None) -> bool:
        with write_connection(self._engine) as connection:
            condition = threads.c.thread_id == thread.thread_id
            if expected_revision is not None:
                condition = condition & (threads.c.revision == expected_revision)
            changed = connection.execute(
                update(threads)
                .where(condition)
                .values(
                    problem=thread.problem,
                    display_name=thread.display_name,
                    scope_json=orjson.dumps(thread.scope).decode(),
                    parent_thread_id=thread.parent_thread_id,
                    fork_origin=thread.fork_origin,
                    lifecycle=thread.lifecycle.value,
                    execution_state=thread.execution_state.value,
                    current_object_ids_json=orjson.dumps(thread.current_object_ids).decode(),
                    working_head_digest=thread.working_head_digest,
                    revision=thread.revision,
                    updated_at=(thread.updated_at or datetime.now(UTC)).isoformat(),
                )
            ).rowcount
        if changed != 1:
            if expected_revision is None:
                raise KeyError(thread.thread_id)
            return False
        return True

    def list(self, project_id: str) -> tuple[WorkThread, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(threads)
                    .where(threads.c.project_id == project_id)
                    .order_by(threads.c.thread_id)
                )
                .mappings()
                .all()
            )
        return tuple(self._deserialize(row) for row in rows)

    @staticmethod
    def _deserialize(row: object) -> WorkThread:
        mapping = cast(dict[str, object], row)
        object_ids = cast(list[object], orjson.loads(str(mapping["current_object_ids_json"])))
        return WorkThread(
            thread_id=str(mapping["thread_id"]),
            project_id=str(mapping["project_id"]),
            cycle_id=str(mapping["cycle_id"]),
            problem=str(mapping["problem"]),
            display_name=str(mapping["display_name"]),
            scope={
                str(key): str(value)
                for key, value in cast(
                    dict[object, object], orjson.loads(str(mapping["scope_json"]))
                ).items()
            },
            parent_thread_id=(
                None if mapping["parent_thread_id"] is None else str(mapping["parent_thread_id"])
            ),
            fork_origin=(None if mapping["fork_origin"] is None else str(mapping["fork_origin"])),
            lifecycle=ThreadLifecycle(str(mapping["lifecycle"])),
            execution_state=ThreadExecutionState(str(mapping["execution_state"])),
            current_object_ids=tuple(str(value) for value in object_ids),
            working_head_digest=str(mapping["working_head_digest"]),
            revision=int(str(mapping["revision"])),
            created_at=datetime.fromisoformat(str(mapping["created_at"])),
            updated_at=datetime.fromisoformat(str(mapping["updated_at"])),
        )
