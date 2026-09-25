"""Exit at durable dispatch boundaries using a local durable invocation counter."""

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.sandbox_service import SandboxExecutionBundle, SandboxService
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL
from thoth.domain.research_request import ResearchAttempt
from thoth.domain.sandbox import SandboxResult, SandboxRunSpec
from thoth.protocol.deferred import current_operation


def durable_json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())


async def main(root: Path, phase: str) -> None:
    patch = pytest.MonkeyPatch()
    runtime, sandbox, project, thread, _ = await prepare_measurement(root, patch)
    auxiliary = ControlledResearchModel()
    structured = A04R2Model.structured

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
        return await structured(self, call)

    patch.setattr(A04R2Model, "structured", combined)
    patch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)
    command = request(
        "thread/input",
        "dispatch-crash",
        {
            "project_id": project,
            "thread_id": thread,
            "contract_version": 2,
            "instruction": "Use the sealed measurement protocol.",
        },
    )
    counter = root / "invocations.json"
    durable_json(counter, {"calls": 0})

    def crash() -> None:
        operation = current_operation.get()
        assert operation is not None
        frame = inspect.currentframe()
        call_stack: list[str] = []
        while frame is not None:
            call_stack.append(
                str(frame.f_globals.get("__name__", "")) + "." + frame.f_code.co_qualname
            )
            frame = frame.f_back
        durable_json(
            root / "dispatch-crash.json",
            {
                "project": project,
                "thread": thread,
                "operation": operation.operation_id,
                "request": command.model_dump(mode="json", by_alias=True),
                "phase": phase,
                "call_stack": call_stack,
            },
        )
        os._exit(37)

    original_run = sandbox.run

    async def run(spec: SandboxRunSpec) -> SandboxResult:
        count = json.loads(counter.read_text(encoding="utf-8"))["calls"]
        durable_json(counter, {"calls": count + 1, "attempt_id": spec.attempt_id})
        if phase == "inside_effect":
            crash()
        return await original_run(spec)

    patch.setattr(sandbox, "run", run)
    service_run = SandboxService.run

    async def service(self: SandboxService, spec: SandboxRunSpec) -> SandboxExecutionBundle:
        if phase == "intent":
            crash()
        return await service_run(self, spec)

    patch.setattr(SandboxService, "run", service)
    journal = RequestRecords.journal

    def before_returned(
        records: RequestRecords, owner: str, key: str, record: BaseModel, state: str = "RUNNING"
    ) -> None:
        if (
            phase == "before_returned"
            and isinstance(record, ResearchAttempt)
            and record.external_effect_state == "RETURNED"
        ):
            crash()
        journal(records, owner, key, record, state)

    patch.setattr(RequestRecords, "journal", before_returned)
    value(await runtime.bus.dispatch(command))
    await runtime.bus.drain()
    raise AssertionError("Dispatch crash boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
