import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.domain.enums import ModelRole
from thoth.domain.memory_preparation import FullMemoryPromotionResult, PreparedMemoryPromotion
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL


@pytest.mark.asyncio
async def test_post_commit_memory_failure_resumes_without_repeating_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, sandbox, project, thread, _ = await prepare_measurement(tmp_path, monkeypatch)
    auxiliary = ControlledResearchModel()
    original_model = A04R2Model.structured

    async def combined(self: A04R2Model, call: ModelRequest[BaseModel]) -> ModelResult[BaseModel]:
        if call.role in {
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.SOURCE_PLANNER,
            ModelRole.HYPOTHESIS_REVIEWER,
        }:
            return await auxiliary.structured(call)
        return await original_model(self, call)

    monkeypatch.setattr(A04R2Model, "structured", combined)
    monkeypatch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)
    original_commit = FullProjectMemoryService.commit_prepared
    fail_once = True

    def commit(
        self: FullProjectMemoryService,
        prepared: PreparedMemoryPromotion,
        *,
        expected_head: str | None = None,
    ) -> FullMemoryPromotionResult:
        nonlocal fail_once
        if fail_once and any(
            (c.source_ref or "").startswith("OUTCOME:r2-outcome:") for c in prepared.candidates
        ):
            fail_once = False
            raise RuntimeError("POST_EXECUTION_MEMORY_COMMIT_FAULT")
        return original_commit(self, prepared, expected_head=expected_head)

    monkeypatch.setattr(FullProjectMemoryService, "commit_prepared", commit)
    try:
        command = request(
            "thread/input",
            "execute-once",
            {
                "project_id": project,
                "thread_id": thread,
                "contract_version": 2,
                "instruction": "Measure using the recorded protocol.",
            },
        )
        accepted = value(await runtime.bus.dispatch(command))
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED"
        payload = cast(dict[str, Any], operation.result)
        learning = payload["post_execution_learning"]
        assert learning["state"] == "HELD" and "COMMIT_FAULT" in learning["reason_code"]
        assert len(sandbox.seen_specs) == 1
        outcome = learning["basis"]["execution"]["outcome_revision_ref"]["revision_digest"]
        assert runtime.ledger.read_revision_by_digest(project, outcome) is not None
        runtime.close()
        runtime = create_runtime(tmp_path / "allowed", sandbox_adapter=sandbox)
        entered, release = asyncio.Event(), asyncio.Event()
        prepare = FullProjectMemoryService.prepare_thread_results

        async def gated(
            self: FullProjectMemoryService, *args: Any, **kwargs: Any
        ) -> PreparedMemoryPromotion:
            entered.set()
            await release.wait()
            return await prepare(self, *args, **kwargs)

        monkeypatch.setattr(FullProjectMemoryService, "prepare_thread_results", gated)
        resuming = asyncio.create_task(
            runtime.bus.dispatch(
                request(
                    "thread/resume", "resume-learning", {"project_id": project, "thread_id": thread}
                )
            )
        )
        await asyncio.wait_for(entered.wait(), 10)
        try:
            concurrent = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/resume",
                        "other-resume",
                        {"project_id": project, "thread_id": thread},
                    )
                )
            )
            assert (
                cast(dict[str, Any], concurrent["post_execution_learning"])["reason_code"]
                == "LEARNING_ALREADY_RUNNING"
            )
        finally:
            release.set()
        resumed = value(await resuming)
        assert resumed["execution_repeated"] is False
        result = cast(dict[str, Any], resumed["post_execution_learning"])
        assert any(m["owner_revision_ref"] == outcome for m in result["promotion"]["committed"])
        assert len(sandbox.seen_specs) == 1
        assert runtime.bus.read_operation(operation.operation_id) == operation
    finally:
        runtime.close()
