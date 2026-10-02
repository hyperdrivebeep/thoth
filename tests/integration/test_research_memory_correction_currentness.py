"""A stored answer that used a memory is marked for review once that memory is corrected."""

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL

AUXILIARY_ROLES = {
    ModelRole.RESEARCH_PLANNER,
    ModelRole.EVIDENCE_RERANKER,
    ModelRole.SEMANTIC_REVIEWER,
    ModelRole.REVIEW_ADJUDICATOR,
    ModelRole.SOURCE_PLANNER,
    ModelRole.HYPOTHESIS_REVIEWER,
}


@pytest.mark.asyncio
async def test_correcting_a_memory_an_answer_used_marks_that_answer_for_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sandbox, project, thread, _ = await prepare_measurement(
        tmp_path, monkeypatch, choose_read_after_test=True
    )
    auxiliary = ControlledResearchModel()
    existing = A04R2Model.structured

    async def combined(self: A04R2Model, call: ModelRequest[BaseModel]) -> ModelResult[BaseModel]:
        if call.role in AUXILIARY_ROLES:
            return await auxiliary.structured(call)
        return await existing(self, call)

    monkeypatch.setattr(A04R2Model, "structured", combined)
    monkeypatch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)

    async def run(key: str, instruction: str) -> dict[str, Any]:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    key,
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "contract_version": 2,
                        "instruction": instruction,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        return cast(dict[str, Any], operation.result)

    async def currentness(key: str) -> dict[str, Any]:
        read = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    key,
                    {"project_id": project, "thread_id": thread, "contract_version": 2},
                )
            )
        )
        return cast(dict[str, Any], read["basis_currentness"])

    try:
        first = await run("memory-seed", "Use the sealed measurement protocol.")
        learned = first["post_execution_learning"]["promotion"]["committed"][0]
        second = await run("memory-use", "Recall the recorded process observation.")
        used = {item["revision_digest"] for item in second["authorized_project_memory"]["included"]}
        assert learned["revision_digest"] in used
        before = await currentness("read-before")
        assert "MEMORY_CORRECTED_AFTER_RESULT" not in before["reasons"]

        correction = value(
            await runtime.bus.dispatch(
                request(
                    "memory/edit/propose",
                    "memory-correct",
                    {
                        "project_id": project,
                        "target_revision_digest": learned["revision_digest"],
                        "corrected_text": "측정 한계를 결론에 함께 적는다",
                        "reason": "처음 기억이 실제 관찰과 다릅니다",
                    },
                )
            )
        )
        assert correction["transition"] == "COMMIT"
        after = await currentness("read-after")
        assert after["state"] == "REVIEW_REQUIRED"
        assert "MEMORY_CORRECTED_AFTER_RESULT" in after["reasons"]
    finally:
        runtime.close()
