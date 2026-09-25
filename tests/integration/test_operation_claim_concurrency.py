from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Any

import pytest
from sqlalchemy import event, func, select
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.storage.schema import idempotency_keys, operations
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse, RpcErrorCode


@pytest.mark.parametrize("case", ["replay", "conflict", "distinct"])
async def test_normal_concurrent_claims_preserve_idempotency_and_project_cas(
    tmp_path: Path, case: str
) -> None:
    workspace = tmp_path / "workspace"
    first, project = await prepare_project(workspace)
    second = create_runtime(workspace)
    first_request = request(
        "project/metadata/update",
        "claim-race",
        {"project_id": project, "expected_revision": 0, "name": "First candidate"},
    )
    second_request = request(
        "project/metadata/update",
        "other-claim" if case == "distinct" else "claim-race",
        {
            "project_id": project,
            "expected_revision": 0,
            "name": "First candidate" if case == "replay" else "Second candidate",
        },
    )
    peer_read = Event()
    lock = Lock()
    reads = 0

    def contention_wait(connection: sqlite3.Connection, record: Any, proxy: Any) -> None:
        del record, proxy
        # This test proves replay/conflict/CAS under overlapping writers, not latency.
        # Keep its deliberate lock-holding barrier separate from the 50ms busy tests.
        # Production connection timeouts are unchanged.
        connection.execute("PRAGMA busy_timeout=15000").close()

    def coordinate(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        nonlocal reads
        if not statement.startswith("SELECT") or "FROM idempotency_keys" not in statement:
            return
        with lock:
            reads += 1
            ordinal = reads
            if ordinal == 2:
                peer_read.set()
        # Force two unprotected lookups to overlap. A correctly serialized writer
        # keeps the peer outside this region, so coordination is bounded.
        if ordinal == 1:
            peer_read.wait(timeout=0.5)

    def dispatch(runtime: AppRuntime, candidate: JsonRpcRequest) -> JsonRpcResponse:
        return asyncio.run(runtime.bus.dispatch(candidate))

    try:
        event.listen(first.ledger.engine, "checkout", contention_wait)
        event.listen(second.ledger.engine, "checkout", contention_wait)
        event.listen(first.ledger.engine, "after_cursor_execute", coordinate)
        event.listen(second.ledger.engine, "after_cursor_execute", coordinate)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = (
                    pool.submit(dispatch, first, first_request),
                    pool.submit(dispatch, second, second_request),
                )
                responses = [future.result(timeout=30) for future in pending]
        finally:
            event.remove(first.ledger.engine, "after_cursor_execute", coordinate)
            event.remove(second.ledger.engine, "after_cursor_execute", coordinate)
        assert reads == 2
        if case == "replay":
            assert all(response.error is None for response in responses)
            results = [response.result for response in responses]
            assert results[0] is not None and results[1] is not None
            assert results[0]["operation_id"] == results[1]["operation_id"]
        else:
            assert sum(response.error is None for response in responses) == 1
            failure = next(response.error for response in responses if response.error is not None)
            assert failure.code == (
                RpcErrorCode.IDEMPOTENCY_CONFLICT
                if case == "conflict"
                else RpcErrorCode.DOMAIN_REJECTED
            )
        with first.ledger.engine.connect() as connection:
            for table in (operations, idempotency_keys):
                count = connection.execute(
                    select(func.count())
                    .select_from(table)
                    .where(
                        table.c.project_id == project,
                        table.c.method == "project/metadata/update",
                    )
                ).scalar_one()
                assert count == (2 if case == "distinct" else 1)
        stored = value(
            await first.bus.dispatch(
                request(
                    "project/read",
                    "check",
                    {
                        "project_id": project,
                    },
                )
            )
        )
        assert stored["revision"] == 1
        assert stored["name"] in {"First candidate", "Second candidate"}
    finally:
        event.remove(first.ledger.engine, "checkout", contention_wait)
        event.remove(second.ledger.engine, "checkout", contention_wait)
        first.close()
        second.close()
    reopened = create_runtime(workspace)
    try:
        restored = value(
            await reopened.bus.dispatch(
                request(
                    "project/read",
                    "reopen",
                    {
                        "project_id": project,
                    },
                )
            )
        )
        assert restored == stored
        if case == "replay":
            replayed = await reopened.bus.dispatch(first_request)
            assert replayed.error is None and replayed.result is not None
            assert replayed.result["state"] == "SUCCEEDED"
            assert replayed.result["value"] == stored
    finally:
        reopened.close()
