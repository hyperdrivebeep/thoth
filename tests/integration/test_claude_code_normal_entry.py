"""Normal THOTH entry consumes a Claude Code route driven by a fake official executable."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.models.claude_code import ClaudeCodeCatalog, ClaudeCodeModel
from thoth.apps.runtime import create_runtime
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability
from thoth.ports.model import ModelPort

pytestmark = pytest.mark.usefixtures("xai_http_guard")
TModel = TypeVar("TModel", bound=BaseModel)

_ELIGIBLE: dict[str, object] = {
    "connected": True,
    "execution_eligible": True,
    "connection_state": "EXECUTION_UNVERIFIED",
}


def _eligible_status(_workspace: Path, **_options: object) -> dict[str, object]:
    return dict(_ELIGIBLE)


class FakeProcess:
    pid = 90001

    def __init__(self, output: dict[str, object]) -> None:
        self.returncode: int | None = 0
        self._output = output

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        assert input is not None and input
        return json.dumps(self._output).encode(), b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


class FakeClaudeCodeRoute(ModelPort):
    """Runs the real ClaudeCodeModel against a fake process that answers like the oracle."""

    def __init__(self, workspace: Path, executable: Path) -> None:
        self.workspace, self.executable = workspace, executable
        self.oracle = ControlledResearchModel()
        self.argv: list[tuple[str, ...]] = []

    @property
    def control_capability(self) -> ModelControlCapability:
        return ClaudeCodeModel.control_capability

    def resolve(self, *, provider: str, model: str | None = None) -> ModelPort:
        assert provider == "claude-code" and model == "sonnet"
        return self

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        oracle = await self.oracle.structured(request)

        async def spawn(argv: tuple[str, ...], env: Mapping[str, str], cwd: Path) -> FakeProcess:
            self.argv.append(argv)
            assert "ANTHROPIC_API_KEY" not in env
            return FakeProcess(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": oracle.output.model_dump(mode="json"),
                    "usage": {"input_tokens": 12, "output_tokens": 5},
                }
            )

        model = ClaudeCodeModel(
            self.workspace,
            model="sonnet",
            executable=self.executable,
            process_factory=spawn,
            status_probe=_eligible_status,
        )
        return await model.structured(request)


@pytest.mark.asyncio
async def test_claude_code_selected_route_runs_a_research_and_persists_after_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-never-reach-cli")
    binary = tmp_path / ("claude.exe" if os.name == "nt" else "claude")
    binary.write_bytes(b"synthetic-binary-never-executed")
    binary.chmod(0o700)
    workspace = tmp_path / "workspace"
    route = FakeClaudeCodeRoute(workspace, binary)
    catalog = ClaudeCodeCatalog(workspace, status=lambda _workspace: dict(_ELIGIBLE))
    runtime = create_runtime(
        workspace,
        model_resolver=route,
        model_catalog=catalog,
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "code-project",
                    {
                        "project_id": "project:claude-code",
                        "name": "Claude Code synthetic",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        selected = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "code-select",
                    {
                        "project_id": "project:claude-code",
                        "expected_digest": None,
                        "selection": {"provider": "claude-code", "model": "sonnet"},
                    },
                )
            )
        )
        assert selected["availability"] == "AVAILABLE"
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "code-start",
                    {
                        "project_id": "project:claude-code",
                        "problem": "Why is latency uncertain?",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(admitted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        assert route.argv
        for argv in route.argv:
            assert argv[argv.index("--model") + 1] == "sonnet"
            assert argv[argv.index("--tools") + 1] == ""
        current = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "code-current",
                    {"project_id": "project:claude-code", "thread_id": admitted["thread_id"]},
                )
            )
        )
        assert current["current_result"]["completion"] == "TERMINAL"
    finally:
        runtime.close()
    reopened = create_runtime(
        workspace,
        model_resolver=route,
        model_catalog=catalog,
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        readback = value(
            await reopened.bus.query(
                request(
                    "thread/read",
                    "code-reopen",
                    {"project_id": "project:claude-code", "thread_id": admitted["thread_id"]},
                )
            )
        )
        assert readback["current_result"]["result"] == current["current_result"]["result"]
    finally:
        reopened.close()
