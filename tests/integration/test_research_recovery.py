import asyncio
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.apps.runtime import AppRuntime, create_runtime


def operation_state(runtime: AppRuntime, operation_id: str) -> str:
    operation = runtime.bus.read_operation(operation_id)
    assert operation is not None
    return operation.state.value


@pytest.mark.asyncio
async def test_pause_is_nonterminal_and_resume_dispatches_same_operation(tmp_path: Path):
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Pause then resume", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 4)
        scope: dict[str, object] = {"project_id": "p", "thread_id": accepted["thread_id"]}
        value(await runtime.bus.dispatch(request("thread/pause", "pause", scope)))
        model.release.set()
        await asyncio.wait_for(runtime.bus.drain(), 8)
        state = value(await runtime.bus.dispatch(request("thread/read", "paused", scope)))
        assert state["execution_state"] == "PAUSED", state
        assert operation_state(runtime, accepted["operation_id"]) == "RUNNING"
        value(await runtime.bus.dispatch(request("thread/resume", "resume", scope)))
        await asyncio.wait_for(runtime.bus.drain(), 10)
        assert operation_state(runtime, accepted["operation_id"]) == "SUCCEEDED"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_second_runtime_waits_and_publishes_only_new_question(tmp_path: Path):
    first_model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, first_model, source=False)
    second_model = ControlledResearchModel()
    second = create_runtime(tmp_path, model_resolver=second_model)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "Old question", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(first_model.started.wait(), 4)
        newer = value(
            await second.bus.dispatch(
                request(
                    "thread/input",
                    "next",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "instruction": "New question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await asyncio.sleep(0.1)
        assert operation_state(second, newer["operation_id"]) == "RUNNING"
        assert not second_model.calls
        first_model.release.set()
        await asyncio.wait_for(asyncio.gather(runtime.bus.drain(), second.bus.drain()), 12)
        assert second_model.calls
        assert operation_state(second, newer["operation_id"]) == "SUCCEEDED"
        assert all("New question" in c.context_pack.problem for c in second_model.calls)
    finally:
        runtime.close()
        second.close()
