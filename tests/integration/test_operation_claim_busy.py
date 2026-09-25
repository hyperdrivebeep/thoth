from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.storage import SqliteProjectStore
from thoth.adapters.storage.schema import idempotency_keys, operations
from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import RpcErrorCode


@pytest.mark.parametrize("lock_kind", ["writer", "reader"])
async def test_claim_busy_is_typed_and_leaves_same_key_available_after_rollback(
    tmp_path: Path,
    lock_kind: str,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    peer = create_runtime(tmp_path)
    inserted: list[str] = []

    def short_wait(connection: sqlite3.Connection, record: Any, proxy: Any) -> None:
        del record, proxy
        connection.execute("PRAGMA busy_timeout=50").close()

    def observe_insert(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        del connection, cursor, parameters, context, many
        if statement.startswith("INSERT INTO operations"):
            inserted.append("inserted-before-commit")

    candidate = request(
        "project/metadata/update",
        "busy-claim",
        {
            "project_id": project,
            "expected_revision": 0,
            "name": "After explicit retry",
        },
    )
    event.listen(runtime.ledger.engine, "checkout", short_wait)
    event.listen(runtime.ledger.engine, "after_cursor_execute", observe_insert)
    try:
        with peer.ledger.engine.connect() as blocker:
            blocker.exec_driver_sql("BEGIN IMMEDIATE" if lock_kind == "writer" else "BEGIN")
            if lock_kind == "reader":
                blocker.exec_driver_sql("SELECT count(*) FROM projects").scalar_one()
            try:
                response = await runtime.bus.dispatch(candidate)
                assert response.error is not None
                assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
                assert response.error.data == {
                    "reason_code": "OPERATION_CLAIM_BUSY",
                    "operation_claimed": False,
                    "retryable": True,
                }
                assert len(inserted) == (1 if lock_kind == "reader" else 0)
                with runtime.ledger.engine.connect() as connection:
                    for table in (operations, idempotency_keys):
                        count = connection.execute(
                            select(func.count())
                            .select_from(table)
                            .where(
                                table.c.project_id == project,
                                table.c.method == "project/metadata/update",
                            )
                        ).scalar_one()
                        assert count == 0
                current = SqliteProjectStore(runtime.ledger.engine).read(project)
                assert current is not None and current.revision == 0
            finally:
                blocker.rollback()
        # The caller explicitly retries; the first request did not dispatch or consume the key.
        result = value(await runtime.bus.dispatch(candidate))
        assert result["revision"] == 1
        assert result["name"] == "After explicit retry"
        with runtime.ledger.engine.connect() as connection:
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(operations)
                    .where(
                        operations.c.project_id == project,
                        operations.c.method == "project/metadata/update",
                    )
                ).scalar_one()
                == 1
            )
    finally:
        event.remove(runtime.ledger.engine, "checkout", short_wait)
        event.remove(runtime.ledger.engine, "after_cursor_execute", observe_insert)
        runtime.close()
        peer.close()


async def test_logical_claim_does_not_join_an_outer_domain_transaction(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)

    def short_wait(connection: sqlite3.Connection, record: Any, proxy: Any) -> None:
        del record, proxy
        connection.execute("PRAGMA busy_timeout=50").close()

    candidate = request(
        "project/metadata/update",
        "nested-claim",
        {
            "project_id": project,
            "expected_revision": 0,
            "name": "Explicit standalone retry",
        },
    )
    event.listen(runtime.ledger.engine, "checkout", short_wait)
    try:
        with runtime.ledger.transaction():
            response = await runtime.bus.dispatch(candidate)
            assert response.error is not None
            assert response.error.data["reason_code"] == "OPERATION_CLAIM_BUSY"
            assert response.error.data["operation_claimed"] is False
        current = SqliteProjectStore(runtime.ledger.engine).read(project)
        assert current is not None and current.revision == 0
        with runtime.ledger.engine.connect() as connection:
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(operations)
                    .where(
                        operations.c.project_id == project,
                        operations.c.method == "project/metadata/update",
                    )
                ).scalar_one()
                == 0
            )
        assert value(await runtime.bus.dispatch(candidate))["revision"] == 1
    finally:
        event.remove(runtime.ledger.engine, "checkout", short_wait)
        runtime.close()
