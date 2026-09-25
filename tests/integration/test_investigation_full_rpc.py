from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_investigation_plan_checkpoint_audit_and_stop_reducer(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    project_id = "project:investigation-full"
    thread_id = "thread:investigation-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "inv-project",
                    {
                        "project_id": project_id,
                        "name": "Investigation full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "inv-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "Why is evidence insufficient?",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        initial_list = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/list",
                    "inv-list-initial",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        initial = cast(list[dict[str, JsonValue]], initial_list["investigations"])
        assert len(initial) == 1
        assert initial[0]["trigger"] == "INITIAL_PROBLEM"

        child_result = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/start",
                    "inv-child",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "trigger": "EVIDENCE_GAP",
                        "question": "Find the missing calibration record",
                        "parent_investigation_id": initial[0]["investigation_id"],
                        "mode": "CRITICAL",
                        "required_evidence_groups": ["calibration", "run manifest"],
                        "query_families": ["project sources", "counter-search"],
                        "budget": 20,
                    },
                )
            )
        )
        child = child_result["investigation"]
        assert isinstance(child, dict)
        investigation_id = str(child["investigation_id"])
        assert child["plan_revision"] == 0

        updated_result = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/update",
                    "inv-update",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation_id,
                        "expected_plan_revision": 0,
                        "query_families": [
                            "project sources",
                            "counter-search",
                            "instrument registry",
                        ],
                        "budget_extension": 10,
                        "reason": "instrument authority source became relevant",
                    },
                )
            )
        )
        updated = updated_result["investigation"]
        assert isinstance(updated, dict)
        assert updated["plan_revision"] == 1
        assert updated["budget"] == 30

        paused_result = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/pause",
                    "inv-pause",
                    {"project_id": project_id, "investigation_id": investigation_id},
                )
            )
        )
        paused = paused_result["investigation"]
        assert isinstance(paused, dict)
        assert paused["execution_state"] == "PAUSED"
        checkpoint_digest = str(paused["checkpoint_digest"])

        resumed_result = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/resume",
                    "inv-resume",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation_id,
                        "expected_checkpoint_digest": checkpoint_digest,
                    },
                )
            )
        )
        resumed = resumed_result["investigation"]
        assert isinstance(resumed, dict)
        assert resumed["execution_state"] == "IDLE"

        stopped_result = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/stop",
                    "inv-stop",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation_id,
                        "reason": "PERMISSION_BLOCKED",
                    },
                )
            )
        )
        stopped = stopped_result["investigation"]
        assert isinstance(stopped, dict)
        assert stopped["domain_state"] == "HOLD"
        assert isinstance(stopped["result"], dict)

        read = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/read",
                    "inv-read",
                    {"project_id": project_id, "investigation_id": investigation_id},
                )
            )
        )
        assert isinstance(read["investigation"], dict)
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/audit/read",
                    "inv-audit",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation_id,
                        "offset": 0,
                        "limit": 100,
                    },
                )
            )
        )
        records = cast(list[dict[str, JsonValue]], audit["records"])
        assert [record["event_type"] for record in records] == [
            "investigation/started",
            "investigation/updated",
            "investigation/checkpointCreated",
            "investigation/checkpointCreated",
            "investigation/completed",
        ]

        saturation = await runtime.bus.dispatch(
            request(
                "investigation/start",
                "inv-saturation-without-opt-in",
                {
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "trigger": "COUNTER_SEARCH",
                    "question": "Search everything",
                    "mode": "SATURATION",
                },
            )
        )
        assert saturation.error is not None
        assert saturation.error.code == -32030
    finally:
        runtime.close()
