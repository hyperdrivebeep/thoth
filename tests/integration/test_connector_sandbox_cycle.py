from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime

from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.domain.canonical import head_set_digest
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
async def test_connector_receipt_and_r2_sandbox_auto_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text("# Evidence\n\nRun a bounded check.\n", encoding="utf-8")
    sandbox = ScriptedSandboxAdapter()
    runtime = create_runtime(workspace, sandbox_adapter=sandbox)
    project_id = "project:connector-sandbox"
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "cs-project",
                    {
                        "project_id": project_id,
                        "name": "Connector sandbox",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        policy = cast(dict[str, JsonValue], created["policy"])
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "cs-source",
                    {
                        "project_id": project_id,
                        "relative_path": "evidence.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], connected["connector_run"])["state"] == "SUCCEEDED"
        assert (
            cast(dict[str, JsonValue], connected["connector_receipt"])["semantic_truth_certified"]
            is False
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "cs-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:connector-sandbox",
                        "problem": "Run a bounded evaluator",
                        "scope": {"workstream": "qa"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], started["current_object_ids"])[0])
        action_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "cs-action",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "primary_purpose": "HYPOTHESIS_DISCRIMINATION",
                        "specification": {
                            "description": "Run evaluator in isolation",
                            "expected_observation_or_change": {"description": "bounded result"},
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["typed terminal result"],
                            "observability": "sandbox receipt",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "runs_untrusted_code": True,
                                "external_write": False,
                            },
                        },
                        "evidence_refs": [],
                    },
                )
            )
        )
        action = cast(dict[str, JsonValue], action_result["action"])
        assert action["risk_tier"] == "R2"
        assert action["authorization_state"] == "NOT_REQUIRED"
        plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "cs-plan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "plan_id": "plan:connector-sandbox",
                        "selected_action_refs": [action["action_id"]],
                        "step_candidates": [
                            {
                                "step_id": "step:sandbox",
                                "action_ref": action["action_id"],
                                "inputs": [],
                                "output_contract": {"type": "sandbox-receipt"},
                                "preconditions": [],
                                "stop_conditions": ["typed terminal result"],
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "runs_untrusted_code": True,
                                    "external_write": False,
                                },
                                "sandbox_spec": {
                                    "runtime_profile": "SCRIPTED",
                                    "image_digest": "scripted:image",
                                    "argv": ["python", "-c", "print(1)"],
                                    "policy_id": policy["policy_id"],
                                    "policy_revision": policy["version"],
                                    "policy_digest": policy["policy_digest"],
                                },
                                "state": "READY",
                            }
                        ],
                        "dependency_edges": [],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], plan_result["plan"])
        execution_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/start",
                    "cs-execution",
                    {
                        "project_id": project_id,
                        "plan_id": plan["plan_id"],
                        "plan_revision_digest": plan["revision_digest"],
                        "execution_profile_ref": "execution:scripted-sandbox-v1",
                        "expected_working_head_digest": head_set_digest(
                            runtime.ledger.read_heads(project_id)
                        ),
                    },
                )
            )
        )
    finally:
        runtime.close()

    execution = cast(dict[str, JsonValue], execution_result["execution"])
    runs = cast(list[dict[str, JsonValue]], execution_result["sandbox_runs"])
    assert execution["state"] == "COMPLETED"
    assert len(runs) == 1
    assert cast(dict[str, JsonValue], runs[0]["result"])["state"] == "SUCCEEDED"
    assert cast(dict[str, JsonValue], runs[0]["receipt"])["semantic_truth_certified"] is False
    assert cast(list[str], runs[0]["observation_refs"])
    observation_artifact = cast(dict[str, JsonValue], runs[0]["observation_artifact"])
    assert str(observation_artifact["source_uri"]).startswith("sandbox://")
    assert cast(list[str], execution["observation_refs"])
    assert len(sandbox.seen_specs) == 1
