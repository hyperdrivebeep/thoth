import asyncio
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.atomicity_observer import observe_validated_child_stack
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_post_learning_running_recovery import NoSecondExecution

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.domain.research_request import ResearchAttempt, ResearchBudget
from thoth.protocol.jsonrpc import JsonRpcRequest


@pytest.mark.parametrize(
    "phase,calls", [("intent", 0), ("inside_effect", 1), ("before_returned", 1)]
)
async def test_restart_never_repeats_ambiguous_dispatch_or_resets_budget(
    tmp_path: Path, phase: str, calls: int
) -> None:
    source_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("execution_crash_worker.py")),
            str(tmp_path),
            phase,
        ],
        cwd=source_root,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(source_root), str(source_root / "src"))),
        },
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 37, completed.stdout + completed.stderr
    marker = json.loads((tmp_path / "dispatch-crash.json").read_text(encoding="utf-8"))
    counter = json.loads((tmp_path / "invocations.json").read_text(encoding="utf-8"))
    assert counter["calls"] == calls
    first_sandbox, second_sandbox = NoSecondExecution(), NoSecondExecution()
    first = create_runtime(tmp_path / "allowed", sandbox_adapter=first_sandbox)
    second = create_runtime(tmp_path / "allowed", sandbox_adapter=second_sandbox)
    try:
        handler = first.bus._registry.resolve("thread/input")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        host = handler.__self__
        attempt = host.records.journal_read(marker["project"], marker["operation"], ResearchAttempt)
        budget = host.records.journal_read(
            marker["project"], f"budget:{marker['thread']}", ResearchBudget
        )
        operation = first.bus.read_operation(marker["operation"])
        assert attempt is not None and budget is not None and operation is not None
        assert (
            attempt.external_effect_state == "DISPATCHING"
            and attempt.remote_observation == "UNKNOWN"
        )
        assert operation.state.value == "RUNNING"
        if calls:
            assert attempt.external_effect_ref == counter["attempt_id"]
        scope = {"project_id": marker["project"], "thread_id": marker["thread"]}
        responses = await asyncio.gather(
            first.bus.dispatch(request("thread/resume", "first-resume", scope)),
            second.bus.dispatch(request("thread/resume", "second-resume", scope)),
        )
        assert all(value(response)["resume_allowed"] is False for response in responses)
        replay = await first.bus.dispatch(JsonRpcRequest.model_validate(marker["request"]))
        assert replay.error is None
        await first.bus.drain()
        assert first.bus.read_operation(marker["operation"]) == operation
        current = host.records.journal_read(marker["project"], marker["operation"], ResearchAttempt)
        assert current is not None and current.external_effect_state == "DISPATCHING"
        assert (
            current.external_effect_ref == attempt.external_effect_ref
            and current.remote_observation == "UNKNOWN"
        )
        assert (
            host.records.journal_read(
                marker["project"], f"budget:{marker['thread']}", ResearchBudget
            )
            == budget
        )
        assert first_sandbox.repeated == second_sandbox.repeated == 0
        assert json.loads((tmp_path / "invocations.json").read_text(encoding="utf-8")) == counter
        expected_runner = "thoth.application.services.research_attempt_runner.run_research_attempt"
        assert expected_runner in marker["call_stack"]
        observe_validated_child_stack(marker["call_stack"])
    finally:
        first.close()
        second.close()
