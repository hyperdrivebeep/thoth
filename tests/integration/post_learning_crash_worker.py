"""Real process exit at the precise post-effect/pre-terminal recovery boundary."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL
from thoth.protocol.deferred import current_operation


async def main(root: Path, phase: str) -> None:
    patch = pytest.MonkeyPatch()
    runtime, sandbox, project, thread, _ = await prepare_measurement(root, patch)
    auxiliary = ControlledResearchModel()
    original = A04R2Model.structured

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
        return await original(self, call)

    patch.setattr(A04R2Model, "structured", combined)
    patch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)
    command = request(
        "thread/input",
        "crash-boundary",
        {
            "project_id": project,
            "thread_id": thread,
            "contract_version": 2,
            "instruction": "Use the sealed measurement protocol.",
        },
    )

    def crash() -> None:
        operation = current_operation.get()
        assert operation is not None
        (root / "crash.json").write_text(
            json.dumps(
                {
                    "project": project,
                    "thread": thread,
                    "operation": operation.operation_id,
                    "sandbox_calls": len(sandbox.seen_specs),
                    "request": command.model_dump(mode="json", by_alias=True),
                    "phase": phase,
                }
            ),
            encoding="utf-8",
        )
        os._exit(37)

    prepare = FullProjectMemoryService.prepare_thread_results

    async def stop_learning(self: FullProjectMemoryService, **kwargs: Any) -> Any:
        if any(
            (c.source_ref or "").startswith("OUTCOME:r2-outcome:") for c in kwargs["candidates"]
        ):
            crash()
        return await prepare(self, **kwargs)

    if phase == "learning":
        patch.setattr(FullProjectMemoryService, "prepare_thread_results", stop_learning)
    else:

        def stop_outcome(*args: Any, **kwargs: Any) -> None:
            crash()

        patch.setattr(R2ClosedLoopCoordinator, "_commit_outcome", stop_outcome)
    value(await runtime.bus.dispatch(command))
    await runtime.bus.drain()
    raise AssertionError("The requested crash boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
