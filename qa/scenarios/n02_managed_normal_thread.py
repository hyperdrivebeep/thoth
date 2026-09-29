from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import orjson
from qa.scenarios.hero_6g_current_core import (
    HeroRunResult,
    default_hero_policy_payload,
    run_current_hero,
)

from thoth.adapters.sandbox import E2BManagedSandboxAdapter
from thoth.ports.model import ModelPort


def managed_policy_payload(*, image_digest: str) -> dict[str, object]:
    payload = default_hero_policy_payload()
    payload["sandbox_runtime_allowlist"] = ["MANAGED"]
    payload["sandbox_network_policy"] = "DENY_ALL"
    payload["sandbox_allowed_hosts"] = []
    payload["sandbox_action_templates"] = [
        {
            "action_family": "SANDBOX_REPLAY",
            "runtime_profile": "MANAGED",
            "image_digest": image_digest,
            "argv": ["printf", "THOTH_MANAGED_NORMAL_THREAD_PASS"],
            "network_policy": "DENY_ALL",
            "allowed_hosts": [],
            "resource_limits": {
                "cpu_millis": 500,
                "wall_seconds": 30,
                "memory_mib": 64,
                "disk_mib": 64,
                "pids": 16,
                "stdout_bytes": 131072,
                "stderr_bytes": 131072,
            },
        }
    ]
    return payload


async def run_managed_normal_thread(
    *,
    workspace: Path,
    adapter: E2BManagedSandboxAdapter,
    model: ModelPort | None = None,
    manifest_path: Path | None = None,
) -> HeroRunResult:
    result = await run_current_hero(
        pack_name="public-demo-membrane",
        workspace=workspace,
        model=model,
        execution_mode="LIVE",
        sandbox_adapter=adapter,
        policy_payload=managed_policy_payload(image_digest=adapter.image_digest),
    )
    manifest = {**result.manifest, "active_sandbox_count": adapter.active_sandbox_count}
    completed = HeroRunResult(manifest=manifest, observations=result.observations)
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(
            orjson.dumps(manifest, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        run_managed_normal_thread(
            workspace=args.workspace,
            adapter=E2BManagedSandboxAdapter(),
            manifest_path=args.manifest,
        )
    )
    print(orjson.dumps(result.manifest, option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
