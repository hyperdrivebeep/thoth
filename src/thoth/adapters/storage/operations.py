from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

import orjson
from pydantic import JsonValue
from sqlalchemy import Connection, Engine, Table, func, insert, select, update
from sqlalchemy.exc import OperationalError

from thoth.adapters.storage.resource_scope_schema import operation_resource_bindings
from thoth.adapters.storage.schema import (
    idempotency_keys,
    memory_records,
    memory_revision_ledger,
    memory_transition_receipts,
    operations,
    receipts,
    semantic_revisions,
    thread_inputs,
    working_heads,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import OperationState
from thoth.domain.operation import (
    InternalFailureDiagnostic,
    InternalFailureStateSnapshot,
    OperationRecord,
)
from thoth.domain.resource_scope import (
    OperationResourceBinding,
    OperationResourceBindingBody,
    ResourceScopeError,
    current_resource_uses,
)
from thoth.ports.operation import OperationClaimBusy, OperationStorePort


class IdempotencyConflict(ValueError):
    def __init__(self, existing: OperationRecord) -> None:
        super().__init__("idempotency key was already used for a different request scope")
        self.existing = existing


def _json_dump(value: dict[str, JsonValue]) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def _json_load(value: str | None) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    loaded = cast(object, orjson.loads(value))
    if not isinstance(loaded, dict):
        raise ValueError("stored operation payload is not an object")
    mapping = cast(dict[object, JsonValue], loaded)
    return {str(key): child for key, child in mapping.items()}


class SqliteOperationStore(OperationStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        database = engine.url.database
        self._diagnostic_path = (
            None
            if database is None or database == ":memory:"
            else Path(database).resolve().parent.parent / "diagnostics" / "internal-failures.jsonl"
        )

    def capture_internal_failure_state(self, *, project_id: str) -> InternalFailureStateSnapshot:
        with self._engine.connect() as connection:
            return InternalFailureStateSnapshot(
                working_head_count=self._project_count(connection, working_heads, project_id),
                semantic_revision_count=self._project_count(
                    connection, semantic_revisions, project_id
                ),
                memory_record_count=self._project_count(connection, memory_records, project_id),
                memory_revision_count=self._project_count(
                    connection, memory_revision_ledger, project_id
                ),
                canonical_receipt_count=self._project_count(connection, receipts, project_id),
                memory_receipt_count=self._project_count(
                    connection, memory_transition_receipts, project_id
                ),
            )

    def is_thread_input_consumed(self, *, project_id: str, thread_id: str) -> bool:
        with self._engine.connect() as connection:
            state = connection.execute(
                select(thread_inputs.c.state)
                .where(
                    thread_inputs.c.project_id == project_id,
                    thread_inputs.c.thread_id == thread_id,
                )
                .order_by(thread_inputs.c.ordinal.desc())
                .limit(1)
            ).scalar_one_or_none()
        return state == "CONSUMED"

    def persist_internal_failure(self, diagnostic: InternalFailureDiagnostic) -> None:
        if self._diagnostic_path is None:
            return
        self._diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        payload = orjson.dumps(diagnostic.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS)
        descriptor = os.open(
            self._diagnostic_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_BINARY,
            0o600,
        )
        try:
            os.write(descriptor, payload + b"\n")
        finally:
            os.close(descriptor)

    @staticmethod
    def _project_count(connection: Connection, table: Table, project_id: str) -> int:
        value = connection.execute(
            select(func.count()).select_from(table).where(table.c.project_id == project_id)
        ).scalar_one()
        return int(value)

    def claim(self, candidate: OperationRecord) -> tuple[OperationRecord, bool]:
        try:
            return self._claim(candidate)
        except OperationalError as exc:
            code = getattr(exc.orig, "sqlite_errorcode", None)
            if isinstance(code, int) and code & 0xFF in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                # _claim has exited its transaction/connection context, so any partial
                # Operation/key writes are rolled back before this reaches the caller.
                raise OperationClaimBusy("OPERATION_CLAIM_BUSY") from exc
            raise

    def _claim(self, candidate: OperationRecord) -> tuple[OperationRecord, bool]:
        # Logical operation bookkeeping owns its transaction; it must not borrow a
        # domain transaction that could later commit a partially failed claim.
        with self._engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            row = (
                connection.execute(
                    select(idempotency_keys).where(
                        idempotency_keys.c.project_id == candidate.project_id,
                        idempotency_keys.c.method == candidate.method,
                        idempotency_keys.c.idempotency_key == candidate.idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                existing = self._read_with_connection(connection, str(row["operation_id"]))
                if existing is None:
                    raise RuntimeError("idempotency record points to a missing operation")
                if str(row["scope_digest"]) != candidate.scope_digest:
                    raise IdempotencyConflict(existing)
                return existing, True

            connection.execute(
                insert(operations).values(
                    operation_id=candidate.operation_id,
                    project_id=candidate.project_id,
                    method=candidate.method,
                    idempotency_key=candidate.idempotency_key,
                    scope_digest=candidate.scope_digest,
                    owner_actor_id=candidate.owner_actor_id,
                    owner_session_id=candidate.owner_session_id,
                    owner_role_assignment_id=candidate.owner_role_assignment_id,
                    owner_data_scopes_json=orjson.dumps(
                        candidate.owner_data_scopes, option=orjson.OPT_SORT_KEYS
                    ).decode(),
                    state=candidate.state.value,
                    result_json=None,
                    error_json=None,
                    created_at=candidate.created_at.isoformat(),
                    completed_at=None,
                    epoch=candidate.epoch,
                )
            )
            connection.execute(
                insert(idempotency_keys).values(
                    project_id=candidate.project_id,
                    method=candidate.method,
                    idempotency_key=candidate.idempotency_key,
                    scope_digest=candidate.scope_digest,
                    operation_id=candidate.operation_id,
                )
            )
            return candidate, False

    def complete(
        self,
        operation_id: str,
        result: dict[str, JsonValue],
        *,
        completed_at: datetime,
    ) -> OperationRecord:
        return self._terminal(
            operation_id,
            OperationState.SUCCEEDED,
            result=result,
            error=None,
            completed_at=completed_at,
        )

    def fail(
        self,
        operation_id: str,
        error: dict[str, JsonValue],
        *,
        completed_at: datetime,
    ) -> OperationRecord:
        return self._terminal(
            operation_id,
            OperationState.FAILED,
            result=None,
            error=error,
            completed_at=completed_at,
        )

    def read(self, operation_id: str) -> OperationRecord | None:
        with read_connection(self._engine) as connection:
            return self._read_with_connection(connection, operation_id)

    def list_by_project(
        self,
        project_id: str,
        *,
        idempotency_prefix: str | None = None,
    ) -> tuple[OperationRecord, ...]:
        statement = select(operations).where(operations.c.project_id == project_id)
        if idempotency_prefix is not None:
            statement = statement.where(operations.c.idempotency_key.startswith(idempotency_prefix))
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    statement.order_by(operations.c.created_at, operations.c.operation_id)
                )
                .mappings()
                .all()
            )
            return tuple(
                self._with_resource_binding(connection, self._deserialize(row)) for row in rows
            )

    def cancel(self, operation_id: str, *, completed_at: datetime) -> OperationRecord:
        with self._engine.begin() as connection:
            current = self._read_with_connection(connection, operation_id)
            if current is None:
                raise KeyError(operation_id)
            if current.state not in {OperationState.PENDING, OperationState.RUNNING}:
                return current
            connection.execute(
                update(operations)
                .where(operations.c.operation_id == operation_id)
                .values(
                    state=OperationState.CANCELLED.value,
                    completed_at=completed_at.isoformat(),
                )
            )
            updated = self._read_with_connection(connection, operation_id)
            if updated is None:
                raise RuntimeError("cancelled operation disappeared")
            return updated

    def _terminal(
        self,
        operation_id: str,
        state: OperationState,
        *,
        result: dict[str, JsonValue] | None,
        error: dict[str, JsonValue] | None,
        completed_at: datetime,
    ) -> OperationRecord:
        with write_connection(self._engine) as connection:
            changed = connection.execute(
                update(operations)
                .where(
                    operations.c.operation_id == operation_id,
                    operations.c.state.in_(
                        (OperationState.PENDING.value, OperationState.RUNNING.value)
                    ),
                )
                .values(
                    state=state.value,
                    result_json=None if result is None else _json_dump(result),
                    error_json=None if error is None else _json_dump(error),
                    completed_at=completed_at.isoformat(),
                )
            ).rowcount
            if changed != 1:
                existing = self._read_with_connection(connection, operation_id)
                if existing is None:
                    raise KeyError(operation_id)
                return existing
            uses = current_resource_uses()
            if uses is not None:
                row = connection.execute(
                    select(operations.c.project_id, operations.c.scope_digest).where(
                        operations.c.operation_id == operation_id,
                    )
                ).one()
                output = result if result is not None else error
                assert output is not None
                body = OperationResourceBindingBody(
                    operation_id=operation_id,
                    project_id=str(row.project_id),
                    request_digest=str(row.scope_digest),
                    output_kind="RESULT" if result is not None else "ERROR",
                    output_digest=domain_digest(
                        "OPERATION_RESOURCE_OUTPUT", "1.0.0", canonical_payload(output)
                    ),
                    resource_uses=uses,
                ).model_dump(mode="python")
                binding = OperationResourceBinding.model_validate(
                    {
                        **body,
                        "binding_digest": domain_digest(
                            "OPERATION_RESOURCE_BINDING", "1.0.0", canonical_payload(body)
                        ),
                    }
                )
                connection.execute(
                    insert(operation_resource_bindings).values(
                        operation_id=operation_id,
                        project_id=binding.project_id,
                        binding_digest=binding.binding_digest,
                        content_json=binding.model_dump_json(),
                    )
                )
            record = self._read_with_connection(connection, operation_id)
            if record is None:
                raise RuntimeError("completed operation disappeared")
            return record

    def _read_with_connection(
        self, connection: Connection, operation_id: str
    ) -> OperationRecord | None:
        row = (
            connection.execute(select(operations).where(operations.c.operation_id == operation_id))
            .mappings()
            .first()
        )
        if row is None:
            return None
        return self._with_resource_binding(connection, self._deserialize(row))

    @staticmethod
    def _with_resource_binding(
        connection: Connection, operation: OperationRecord
    ) -> OperationRecord:
        row = (
            connection.execute(
                select(operation_resource_bindings).where(
                    operation_resource_bindings.c.operation_id == operation.operation_id,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return operation
        try:
            binding = OperationResourceBinding.model_validate_json(str(row["content_json"]))
        except ValueError as exc:
            raise ResourceScopeError("OPERATION_RESOURCE_BINDING_INVALID") from exc
        output = operation.result if binding.output_kind == "RESULT" else operation.error
        if (
            binding.project_id != operation.project_id
            or binding.operation_id != operation.operation_id
            or str(row["project_id"]) != operation.project_id
            or str(row["binding_digest"]) != binding.binding_digest
            or binding.request_digest != operation.scope_digest
            or output is None
            or binding.output_digest
            != domain_digest("OPERATION_RESOURCE_OUTPUT", "1.0.0", canonical_payload(output))
        ):
            raise ResourceScopeError("OPERATION_RESOURCE_BINDING_INVALID")
        return operation.model_copy(update={"resource_uses": binding.resource_uses})

    @staticmethod
    def _deserialize(row: object) -> OperationRecord:
        mapping = cast(dict[str, object], row)
        return OperationRecord(
            operation_id=str(mapping["operation_id"]),
            project_id=str(mapping["project_id"]),
            method=str(mapping["method"]),
            idempotency_key=str(mapping["idempotency_key"]),
            scope_digest=str(mapping["scope_digest"]),
            owner_actor_id=(
                None if mapping["owner_actor_id"] is None else str(mapping["owner_actor_id"])
            ),
            owner_session_id=(
                None if mapping["owner_session_id"] is None else str(mapping["owner_session_id"])
            ),
            owner_role_assignment_id=(
                None
                if mapping["owner_role_assignment_id"] is None
                else str(mapping["owner_role_assignment_id"])
            ),
            owner_data_scopes=tuple(
                str(item)
                for item in cast(list[object], orjson.loads(str(mapping["owner_data_scopes_json"])))
            ),
            state=OperationState(str(mapping["state"])),
            result=_json_load(cast(str | None, mapping["result_json"])),
            error=_json_load(cast(str | None, mapping["error_json"])),
            created_at=datetime.fromisoformat(str(mapping["created_at"])),
            completed_at=(
                None
                if mapping["completed_at"] is None
                else datetime.fromisoformat(str(mapping["completed_at"]))
            ),
            epoch=int(cast(int, mapping["epoch"])),
        )
