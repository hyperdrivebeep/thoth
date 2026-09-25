from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.adapters.storage import SqliteOperationStore
from thoth.apps.runtime import create_runtime
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.protocol.jsonrpc import JsonRpcRequest


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


@pytest.mark.asyncio
async def test_running_operation_can_be_cancelled_but_terminal_operation_is_unchanged(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    store = SqliteOperationStore(runtime.ledger.engine)
    running = OperationRecord(
        operation_id="operation:running",
        project_id="project:cancel",
        method="fixture/run",
        idempotency_key="fixture-running",
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )
    terminal = running.model_copy(
        update={
            "operation_id": "operation:done",
            "idempotency_key": "fixture-done",
            "scope_digest": "b" * 64,
            "state": OperationState.SUCCEEDED,
            "result": {"ok": True},
        }
    )
    store.claim(running)
    store.claim(terminal)
    try:
        cancelled = await runtime.bus.dispatch(
            _rpc(
                "operation/cancel",
                "cancel-running",
                {"project_id": "project:cancel", "operation_id": "operation:running"},
            )
        )
        unchanged = await runtime.bus.dispatch(
            _rpc(
                "operation/cancel",
                "cancel-done",
                {"project_id": "project:cancel", "operation_id": "operation:done"},
            )
        )
    finally:
        runtime.close()

    assert cancelled.result is not None
    cancelled_value = cancelled.result["value"]
    assert isinstance(cancelled_value, dict)
    assert cancelled_value["state"] == "CANCELLED"
    assert cancelled_value["cancelled"] is True
    assert unchanged.result is not None
    unchanged_value = unchanged.result["value"]
    assert isinstance(unchanged_value, dict)
    assert unchanged_value["state"] == "SUCCEEDED"
    assert unchanged_value["cancelled"] is False
