"""A model call that was cut off shows as a failure in thread/read; an ordinary hold does not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelExecutionHold


class CutOffModel(ControlledResearchModel):
    async def structured(self, request: ModelRequest[Any]) -> ModelResult[Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        if request.role == ModelRole.SEMANTIC_REVIEWER:
            raise ModelExecutionHold("OAUTH_TRANSPORT_FAILURE")
        return await super().structured(request)


async def _read(runtime: Any, model_start: str) -> dict[str, Any]:
    started = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                model_start,
                {
                    "project_id": "p",
                    "problem": "LAB-42 지연 조건을 요약해줘",
                    "contract_version": 2,
                },
            )
        )
    )
    await runtime.bus.drain()
    return value(
        await runtime.bus.query(
            request("thread/read", "r", {"project_id": "p", "thread_id": started["thread_id"]})
        )
    )


@pytest.mark.asyncio
async def test_a_cut_off_model_call_is_reported_as_a_model_call_failure(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, CutOffModel(), source=True)
    try:
        read = await _read(runtime, "cut")
        # the operation keeps its published meaning: a hold result was stored
        assert read["operation_state"] == "SUCCEEDED"
        assert read["answer_outcome"]["has_answer"] is False
        failure = read["failure"]
        assert failure is not None
        assert failure["primary"]["origin"] == "MODEL_CALL"
        assert failure["primary"]["reason_code"] == "OAUTH_TRANSPORT_FAILURE"
        assert failure["retry_requires_user_action"] is True
        assert read["execution_summary"]["automatic_retry"] is False
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_ordinary_evidence_hold_is_not_reported_as_a_failure(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(missing=True), source=True)
    try:
        read = await _read(runtime, "ordinary")
        assert read["operation_state"] == "SUCCEEDED"
        assert read["failure"] is None
    finally:
        runtime.close()
