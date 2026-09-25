import asyncio
import inspect
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.apps.runtime import create_runtime
from thoth.domain.research_request import ResearchBudget


async def test_legacy_call_limit_does_not_stop_roles_and_reopen_uses_no_model_calls(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    admitted = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "stage-case",
                {
                    "project_id": "p",
                    "problem": "latency in connected records only",
                    "contract_version": 2,
                },
            )
        )
    )
    await asyncio.wait_for(model.started.wait(), 10)
    handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
    assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
    records = handler.__self__.records
    budget = records.journal_read("p", f"budget:{admitted['thread_id']}", ResearchBudget)
    assert budget is not None
    records.journal(
        "p", f"budget:{admitted['thread_id']}", budget.model_copy(update={"max_calls": 2})
    )
    model.release.set()
    await runtime.bus.drain()
    read = request("thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]})
    state = value(await runtime.bus.query(read))
    assert state["current_result"]["terminal_reason"] == "BOUNDED_RESEARCH_COMPLETE"
    assert [s["role"] for s in state["completed_stages"]][:4] == [
        "RESEARCH_PLANNER",
        "EVIDENCE_RERANKER",
        "SEMANTIC_REVIEWER",
        "REVIEW_ADJUDICATOR",
    ]
    assert state["answer_outcome"]["has_answer"] is True
    assert state["resume_information"]["additional_budget_required"] is False
    assert all("usage_observation" in call.context_pack.research_context for call in model.calls)
    assert state["resume_information"]["same_attempt_after_terminal_supported"] is False
    assert state["resume_information"]["automatic_new_budget"] is False
    runtime.close()
    other = ControlledResearchModel()
    reopened = create_runtime(tmp_path, model_resolver=other)
    try:
        again = value(await reopened.bus.query(read))
        assert again["completed_stages"] == state["completed_stages"]
        assert again["current_result"] == state["current_result"]
        assert not other.calls
    finally:
        reopened.close()


async def test_stage_event_failure_rolls_back_output_and_blocks_next_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model)
    try:
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        events = handler.__self__.records.events
        assert events is not None
        append = events.append

        def fail(*args: object, **kwargs: object):
            if kwargs.get("event_type") == "research.stage.completed":
                raise RuntimeError("CONTROLLED_STAGE_WRITE_FAILURE")
            return append(*args, **kwargs)  # type: ignore

        monkeypatch.setattr(events, "append", fail)
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "stage-fault",
                    {"project_id": "p", "problem": "latency", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert state["operation_state"] == "FAILED"
        assert state["completed_stages"] == []
        assert len(model.calls) == 1
        snapshots = runtime.ledger.read_heads("p")
        assert not any("research-stage:" in key for key in snapshots)
    finally:
        runtime.close()


async def test_source_detach_during_model_wait_cannot_publish_a_late_stage(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "source-fence",
                    {"project_id": "p", "problem": "latency", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        binding = handler.__self__.governance.list_source_bindings("p")[0]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "detach",
                    {"project_id": "p", "binding_id": binding.binding_id, "mode": "DETACH"},
                )
            )
        )
        model.release.set()
        await runtime.bus.drain()
        result = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert result["operation_state"] == "CANCELLED"
        assert result["completed_stages"] == []
        assert result["answer_outcome"]["has_answer"] is False
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()
