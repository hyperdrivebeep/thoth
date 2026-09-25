from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from thoth.adapters.sandbox import (
    DockerSandboxAdapter,
    E2BManagedSandboxAdapter,
    FirecrackerSandboxAdapter,
    GVisorSandboxAdapter,
)
from thoth.apps.runtime import create_runtime
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
    if response.error is not None or response.result is None:
        raise RuntimeError(response.model_dump_json())
    child = response.result["value"]
    if not isinstance(child, dict):
        raise RuntimeError("RPC result is not an object")
    return cast(dict[str, JsonValue], child)


async def run(
    runtime_name: str,
    image: str,
    run_root: Path,
    *,
    firecracker_binary: Path | None = None,
    firecracker_config: Path | None = None,
) -> dict[str, object]:
    adapter = (
        DockerSandboxAdapter(run_root)
        if runtime_name == "docker"
        else GVisorSandboxAdapter(run_root)
        if runtime_name == "gvisor"
        else FirecrackerSandboxAdapter(
            firecracker_binary=_required_path(firecracker_binary, "firecracker binary"),
            config_path=_required_path(firecracker_config, "firecracker config"),
            run_root=run_root,
            expected_marker="THOTH_FIRECRACKER_GUEST_PASS",
        )
        if runtime_name == "firecracker"
        else E2BManagedSandboxAdapter()
    )
    effective_image = (
        adapter.image_digest
        if isinstance(adapter, FirecrackerSandboxAdapter | E2BManagedSandboxAdapter)
        else image
    )
    with tempfile.TemporaryDirectory(prefix=f"thoth-{runtime_name}-") as temporary:
        runtime = create_runtime(Path(temporary), sandbox_adapter=adapter)
        project_id = f"project:live-{runtime_name}"
        try:
            created = value(
                await runtime.bus.dispatch(
                    request(
                        "project/create",
                        f"{runtime_name}-project",
                        {
                            "project_id": project_id,
                            "name": f"Live {runtime_name}",
                            "cutoff_at": "2026-08-31T00:00:00Z",
                        },
                    )
                )
            )
            policy = cast(dict[str, JsonValue], created["policy"])
            started = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/start",
                        f"{runtime_name}-thread",
                        {
                            "project_id": project_id,
                            "thread_id": f"thread:live-{runtime_name}",
                            "problem": "Execute one digest-pinned harmless R2 fixture",
                            "scope": {"workstream": "external-sandbox-qa"},
                        },
                    )
                )
            )
            object_id = str(cast(list[str], started["current_object_ids"])[0])
            action_result = value(
                await runtime.bus.dispatch(
                    request(
                        "action/create",
                        f"{runtime_name}-action",
                        {
                            "project_id": project_id,
                            "object_id": object_id,
                            "primary_purpose": "HYPOTHESIS_DISCRIMINATION",
                            "specification": {
                                "description": "Run harmless fixture in isolation",
                                "expected_observation_or_change": {
                                    "description": "hello-world exits zero"
                                },
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
            plan_result = value(
                await runtime.bus.dispatch(
                    request(
                        "action/plan/compose",
                        f"{runtime_name}-plan",
                        {
                            "project_id": project_id,
                            "object_id": object_id,
                            "plan_id": f"plan:live-{runtime_name}",
                            "selected_action_refs": [action["action_id"]],
                            "step_candidates": [
                                {
                                    "step_id": f"step:live-{runtime_name}",
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
                                        "runtime_profile": (
                                            "DOCKER_POC"
                                            if runtime_name == "docker"
                                            else "GVISOR"
                                            if runtime_name == "gvisor"
                                            else "FIRECRACKER"
                                            if runtime_name == "firecracker"
                                            else "MANAGED"
                                        ),
                                        "image_digest": effective_image,
                                        "argv": (
                                            ["/hello"]
                                            if runtime_name in {"docker", "gvisor"}
                                            else ["boot"]
                                            if runtime_name == "firecracker"
                                            else ["printf", "THOTH_MANAGED_PASS"]
                                        ),
                                        "policy_id": policy["policy_id"],
                                        "policy_revision": policy["version"],
                                        "policy_digest": policy["policy_digest"],
                                        "resource_limits": {
                                            "cpu_millis": 500,
                                            "memory_mib": 64,
                                            "pids": 32,
                                            "disk_mib": 64,
                                            "wall_seconds": 30,
                                            "stdout_bytes": 131072,
                                            "stderr_bytes": 131072,
                                        },
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
                        f"{runtime_name}-execution",
                        {
                            "project_id": project_id,
                            "plan_id": plan["plan_id"],
                            "plan_revision_digest": plan["revision_digest"],
                            "execution_profile_ref": f"execution:{runtime_name}-live-v1",
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
    sandbox_runs = cast(list[dict[str, JsonValue]], execution_result["sandbox_runs"])
    if len(sandbox_runs) != 1:
        raise RuntimeError("expected exactly one sandbox run")
    run_value = sandbox_runs[0]
    result = cast(dict[str, JsonValue], run_value["result"])
    receipt = cast(dict[str, JsonValue], run_value["receipt"])
    if execution["state"] != "COMPLETED" or result["state"] != "SUCCEEDED":
        raise RuntimeError(json.dumps(execution_result, ensure_ascii=False))
    return {
        "runtime": runtime_name,
        "execution_state": execution["state"],
        "sandbox_state": result["state"],
        "cleanup_state": result["cleanup_state"],
        "exit_code": result["exit_code"],
        "receipt_digest": receipt["receipt_digest"],
        "semantic_truth_certified": receipt["semantic_truth_certified"],
        "observation_refs": run_value["observation_refs"],
        "image_digest": effective_image,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime",
        choices=("docker", "gvisor", "firecracker", "managed"),
        required=True,
    )
    parser.add_argument("--image", default="unused-for-firecracker")
    parser.add_argument("--run-root", type=Path, default=Path("/var/lib/thoth-sandbox-runs"))
    parser.add_argument("--firecracker-binary", type=Path)
    parser.add_argument("--firecracker-config", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                run(
                    args.runtime,
                    args.image,
                    args.run_root,
                    firecracker_binary=args.firecracker_binary,
                    firecracker_config=args.firecracker_config,
                )
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _required_path(value: Path | None, label: str) -> Path:
    if value is None:
        raise ValueError(f"{label} is required")
    return value


if __name__ == "__main__":
    main()
