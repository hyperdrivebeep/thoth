from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def _request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def _value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    value = response.result["value"]
    assert isinstance(value, dict)
    return cast(dict[str, JsonValue], value)


@pytest.mark.asyncio
async def test_closure_and_local_export_are_revisioned_and_receipted(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    project_id = "project:lifecycle"
    try:
        _value(
            await runtime.bus.dispatch(
                _request(
                    "project/create",
                    "create-lifecycle",
                    {
                        "project_id": project_id,
                        "name": "Lifecycle fixture",
                        "cutoff_at": "2026-08-30T12:00:00Z",
                    },
                )
            )
        )
        blocked = _value(
            await runtime.bus.dispatch(
                _request(
                    "closure/prepare",
                    "prepare-blocked",
                    {
                        "project_id": project_id,
                        "resolution": "analysis is not ready",
                        "unresolved_refs": ["evidence:missing"],
                    },
                )
            )
        )
        blocked_closure = blocked["closure"]
        assert isinstance(blocked_closure, dict)
        assert blocked_closure["status"] == "BLOCKED"

        ready = _value(
            await runtime.bus.dispatch(
                _request(
                    "closure/prepare",
                    "prepare-ready",
                    {
                        "project_id": project_id,
                        "resolution": "bounded local cycle completed",
                    },
                )
            )
        )
        ready_closure = ready["closure"]
        assert isinstance(ready_closure, dict)
        closure_id = ready_closure["closure_id"]
        assert isinstance(closure_id, str)
        current = _value(
            await runtime.bus.dispatch(
                _request(
                    "closure/read",
                    "read-ready",
                    {"project_id": project_id, "closure_id": closure_id},
                )
            )
        )
        head = current["head_digest"]
        assert isinstance(head, str)
        finalized = _value(
            await runtime.bus.dispatch(
                _request(
                    "closure/finalize",
                    "finalize-ready",
                    {
                        "project_id": project_id,
                        "closure_id": closure_id,
                        "expected_current_head": head,
                    },
                )
            )
        )
        closed = finalized["closure"]
        assert isinstance(closed, dict)
        assert closed["status"] == "CLOSED"

        prepared_export = _value(
            await runtime.bus.dispatch(
                _request(
                    "export/prepare",
                    "prepare-export",
                    {
                        "project_id": project_id,
                        "purpose": "local field validation handoff",
                        "audience": "authorized evaluator",
                    },
                )
            )
        )
        export = prepared_export["export"]
        assert isinstance(export, dict)
        assert export["release_state"] == "LOCAL_SEALED"
        listed = _value(
            await runtime.bus.dispatch(
                _request(
                    "export/list",
                    "list-exports",
                    {"project_id": project_id},
                )
            )
        )
        exports = listed["exports"]
        assert isinstance(exports, list)
        assert len(exports) == 1
        receipts = runtime.ledger.read_receipts(project_id)
        assert [receipt.receipt_type.value for receipt in receipts] == [
            "CLOSURE",
            "CLOSURE",
            "CLOSURE",
            "EXPORT",
        ]
    finally:
        runtime.close()
