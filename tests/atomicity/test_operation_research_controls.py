"""Public operation controls consume the real v2 research continuation."""

import asyncio
from pathlib import Path

from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.apps.runtime import create_runtime


async def test_operation_pause_and_resume_preserve_original_request_and_budget(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Explain LAB-42 latency.",
                        "contract_version": 2,
                    },
                )
            )
        )
        operation_id, thread_id = str(accepted["operation_id"]), str(accepted["thread_id"])
        await asyncio.wait_for(model.started.wait(), timeout=10)
        paused = value(
            await runtime.bus.dispatch(
                request(
                    "operation/pause",
                    "pause",
                    {
                        "project_id": "p",
                        "operation_id": operation_id,
                    },
                )
            )
        )
        assert paused["pause_accepted"] is True and paused["operation_id"] == operation_id
        model.release.set()
        await runtime.bus.drain()
        thread_input: dict[str, object] = {"project_id": "p", "thread_id": thread_id}
        paused_thread = value(
            await runtime.bus.query(request("thread/read", "paused", thread_input))
        )
        assert paused_thread["execution_state"] == "PAUSED"
        paused_calls = paused_thread["budget"]["calls"]
        assert paused_calls > 0
        original = runtime.bus.read_operation(operation_id)
        assert original is not None and original.state.value == "RUNNING"
        checkpoint = value(
            await runtime.bus.query(
                request(
                    "operation/checkpoint/read",
                    "checkpoint",
                    {
                        "project_id": "p",
                        "operation_id": operation_id,
                    },
                )
            )
        )["checkpoint"]["checkpoint_digest"]
        stale = await runtime.bus.dispatch(
            request(
                "operation/resume",
                "stale",
                {
                    "project_id": "p",
                    "operation_id": operation_id,
                    "expected_checkpoint_digest": "0" * 64,
                },
            )
        )
        assert stale.error is not None and stale.error.code == -32031
        assert runtime.bus.read_operation(operation_id) == original
        resumed = value(
            await runtime.bus.dispatch(
                request(
                    "operation/resume",
                    "resume",
                    {
                        "project_id": "p",
                        "operation_id": operation_id,
                        "expected_checkpoint_digest": checkpoint,
                    },
                )
            )
        )
        assert resumed["resume_allowed"] is True and resumed["operation_id"] == operation_id
        await runtime.bus.drain()
        terminal = runtime.bus.read_operation(operation_id)
        assert terminal is not None and terminal.state.value == "SUCCEEDED", terminal
        completed_thread = value(
            await runtime.bus.query(request("thread/read", "done", thread_input))
        )
        assert completed_thread["budget"]["calls"] >= paused_calls
        assert completed_thread["execution_state"] == "IDLE"
        calls = len(model.calls)
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()
    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        assert reopened.bus.read_operation(operation_id) == terminal
        after = value(await reopened.bus.query(request("thread/read", "reopen", thread_input)))
        assert after["budget"] == completed_thread["budget"]
        assert after["current_result"] == completed_thread["current_result"]
        assert len(model.calls) == calls
    finally:
        reopened.close()
