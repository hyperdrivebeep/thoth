from __future__ import annotations

from pathlib import Path

import pytest

from thoth.apps.runtime import create_runtime
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
async def test_projectpack_list_and_run_use_idempotent_command_bus(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "examples" / "projectpacks"
    runtime = create_runtime(tmp_path / "workspace", projectpack_root=root)
    try:
        listed = await runtime.bus.dispatch(
            _rpc("projectpack/list", "list-packs", {"project_id": "system:projectpacks"})
        )
        run_request = _rpc(
            "projectpack/run",
            "run-demo-pack",
            {
                "project_id": "project:demo-system",
                "pack_name": "demo-system",
                "problem": "Explain the current comparison gap.",
                "scripted": True,
            },
        )
        first = await runtime.bus.dispatch(run_request)
        replay = await runtime.bus.dispatch(run_request)
        objects = await runtime.bus.dispatch(
            _rpc(
                "object/list",
                "list-objects",
                {"project_id": "project:demo-system"},
            )
        )
        preflight = await runtime.bus.dispatch(
            _rpc(
                "action/preflight/read",
                "read-preflight",
                {
                    "project_id": "project:demo-system",
                    "object_id": "object:comparison-gap",
                },
            )
        )
        criteria = await runtime.bus.dispatch(
            _rpc(
                "criteria/list",
                "list-criteria",
                {"project_id": "project:demo-system"},
            )
        )
    finally:
        runtime.close()

    assert listed.result is not None
    listed_value = listed.result["value"]
    assert isinstance(listed_value, dict)
    packs = listed_value["packs"]
    assert isinstance(packs, list)
    assert any(isinstance(item, dict) and item.get("pack_name") == "demo-system" for item in packs)
    assert first.error is None
    assert first.model_dump(mode="json") == replay.model_dump(mode="json")
    assert first.result is not None
    result_value = first.result["value"]
    assert isinstance(result_value, dict)
    assert result_value["evidence_count"] == 6
    assert objects.result is not None
    object_value = objects.result["value"]
    assert isinstance(object_value, dict)
    object_items = object_value["objects"]
    assert isinstance(object_items, list)
    assert len(object_items) == 1
    assert preflight.result is not None
    preflight_value = preflight.result["value"]
    assert isinstance(preflight_value, dict)
    cards = preflight_value["cards"]
    assert isinstance(cards, list)
    assert len(cards) == 1
    card = cards[0]
    assert isinstance(card, dict)
    assert card["single_use"] is True
    assert card["egress"] == "deny-until-approved"
    assert criteria.result is not None
    criteria_value = criteria.result["value"]
    assert isinstance(criteria_value, dict)
    assert criteria_value["criteria"] == []
