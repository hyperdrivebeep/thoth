import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.domain.sandbox import SandboxResult, SandboxRunSpec
from thoth.protocol.jsonrpc import JsonRpcRequest


class NoSecondExecution(ScriptedSandboxAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.repeated = 0

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        self.repeated += 1
        raise AssertionError("Committed or unknown effects must never be repeated")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,phase",
    [
        ("resume", "learning"),
        ("replay", "learning"),
        ("resume", "outcome"),
        ("paused_replay", "learning"),
        ("during_pause", "learning"),
        ("during_stop", "learning"),
    ],
)
async def test_crash_before_terminal_recovers_only_verified_learning(
    tmp_path: Path,
    route: str,
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = Path(__file__).with_name("post_learning_crash_worker.py")
    source_root = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        [sys.executable, str(worker), str(tmp_path), phase],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=source_root,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(source_root), str(source_root / "src"))),
        },
    )
    assert process.returncode == 37, process.stdout + process.stderr
    marker = json.loads((tmp_path / "crash.json").read_text(encoding="utf-8"))
    assert marker["sandbox_calls"] == 1
    sandbox = NoSecondExecution()
    runtime = create_runtime(tmp_path / "allowed", sandbox_adapter=sandbox)
    try:
        before = runtime.bus.read_operation(marker["operation"])
        assert before is not None and before.state.value == "RUNNING"
        scope = {"project_id": marker["project"], "thread_id": marker["thread"]}
        if route == "paused_replay":
            paused = value(await runtime.bus.dispatch(request("thread/pause", "pause", scope)))
            assert paused["pause_accepted"] is True
            response = await runtime.bus.dispatch(JsonRpcRequest.model_validate(marker["request"]))
            assert response.error is None
            await runtime.bus.drain()
            assert runtime.bus.read_operation(marker["operation"]) == before
            current = value(await runtime.bus.query(request("thread/read", "paused-read", scope)))
            assert current["execution_state"] in {"PAUSED", "PAUSE_PENDING"}
            assert sandbox.repeated == 0
        if route.startswith("during_"):
            original = FullProjectMemoryService.prepare_thread_results
            control = "thread/pause" if route == "during_pause" else "thread/stop"

            async def interrupt(self: FullProjectMemoryService, **kwargs: Any) -> Any:
                monkeypatch.setattr(FullProjectMemoryService, "prepare_thread_results", original)
                interrupted = await runtime.bus.dispatch(request(control, "interrupt", scope))
                assert interrupted.error is None
                return await original(self, **kwargs)

            monkeypatch.setattr(FullProjectMemoryService, "prepare_thread_results", interrupt)
        response = await runtime.bus.dispatch(
            request("thread/resume", "resume", scope)
            if route != "replay"
            else JsonRpcRequest.model_validate(marker["request"])
        )
        assert response.error is None, response.error
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(marker["operation"])
        assert operation is not None and sandbox.repeated == 0
        if route == "during_stop":
            assert operation.state.value == "CANCELLED"
            return
        if route == "during_pause":
            assert operation == before
            current = value(await runtime.bus.query(request("thread/read", "paused-read", scope)))
            assert current["execution_state"] in {"PAUSED", "PAUSE_PENDING"}
            response = await runtime.bus.dispatch(request("thread/resume", "resume-again", scope))
            assert response.error is None
            await runtime.bus.drain()
            operation = runtime.bus.read_operation(marker["operation"])
            assert operation is not None and sandbox.repeated == 0
        if phase == "outcome":
            result = value(response)
            assert result["resume_allowed"] is False
            assert result["reason"] == "EXTERNAL_EFFECT_RECONCILIATION_REQUIRED"
            assert operation.state.value == "RUNNING"
            return
        assert operation.state.value == "SUCCEEDED", operation
        result = cast(dict[str, Any], operation.result)
        assert result["execution_repeated"] is False
        assert result["recovery_scope"] == "POST_EXECUTION_LEARNING_ONLY"
        assert result["research_state"] == "PARTIAL"
        assert result["answer_status"] == "PARTIAL_HOLD"
        assert result["postprocessing_not_verified_in_this_recovery"] == [
            "IMPROVEMENT_OBSERVATION",
            "BASELINE_REFRESH",
        ]
        learning = result["post_execution_learning"]
        assert learning["state"] == "HELD"
        assert learning["reason_code"] == "MEMORY_REVIEW_HELD"
        outcome = learning["basis"]["execution"]["outcome_revision_ref"]["revision_digest"]
        assert any(m["owner_revision_ref"] == outcome for m in learning["promotion"]["committed"])
        current = value(await runtime.bus.query(request("thread/read", "read", scope)))
        assert current["freshness"] == "CURRENT"
        assert current["basis_currentness"]["state"] == "CURRENT"
        assert current["current_result"]["research_basis"]["coverage"] == "COMPLETE"
        assert current["current_result"]["completion"] == "TERMINAL"
        assert current["current_result"]["terminal_reason"] == "POST_EXECUTION_RECOVERED_PARTIAL"
        assert current["execution_state"] == "IDLE"
    finally:
        runtime.close()
