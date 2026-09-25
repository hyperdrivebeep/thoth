"""Isolated HTTP contract fixture. Every model response is a local test double."""

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.adapters.http.app import create_app
from thoth.application.commands.workspace_setup import WorkspaceSetupHandlers
from thoth.apps.research_history_composition import RESTORE_APPLY_READY
from thoth.apps.runtime import create_runtime


async def prepare(workspace: Path, port: int) -> dict[str, object]:
    workspace.mkdir(parents=True, exist_ok=True)
    descriptor = workspace / "fixture.json"
    if descriptor.exists():
        return json.loads(descriptor.read_text(encoding="utf-8"))
    runtime, _model, accepted, candidates = await prepared(workspace)
    try:
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snap, "statement", "Controlled current hypothesis")
        result = {
            "url": f"http://127.0.0.1:{port}",
            "project_id": "p",
            "thread_id": accepted["thread_id"],
            "workspace": str(workspace.resolve()),
            "model": "CONTROLLED_TEST_DOUBLE",
            "apply_ready": False,
            "selection": {
                "project_id": "p",
                "entity_type": revision.entity_type.value,
                "entity_id": revision.entity_id,
                "target_revision_digest": revision.revision_digest,
                "expected_current_head": changed.revision_digest,
            },
        }
        descriptor.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    descriptor = asyncio.run(prepare(args.workspace, args.port))
    descriptor["apply_ready"] = RESTORE_APPLY_READY
    (args.workspace / "fixture.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    print(json.dumps(descriptor), flush=True)
    runtime = create_runtime(
        args.workspace,
        model_resolver=ControlledResearchModel(one=True),
        resource_scope_policy=fixture_scope_policy(),
    )
    setup = WorkspaceSetupHandlers(root=args.workspace, model_connected=lambda: True)
    runtime.bus._registry.decorate("workspace/ready", lambda _: setup.ready)
    # The test double is connected without credentials; its explicit fixture banner remains.
    value(
        asyncio.run(
            runtime.bus.dispatch(
                request(
                    "workspace/setup/update", "fixture-internet-off", {"internet_consent": "DENIED"}
                )
            )
        )
    )
    try:
        uvicorn.run(create_app(runtime.bus), host="127.0.0.1", port=args.port)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
