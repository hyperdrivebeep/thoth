"""A real request that the provider refuses for the model or account marks that model.

Nothing else is marked.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.apps.runtime import create_runtime
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_catalog import is_model_rejection
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model import ModelExecutionHold

pytestmark = pytest.mark.usefixtures("xai_http_guard")


class RecordingCatalog(StaticModelCatalog):
    def __init__(self) -> None:
        super().__init__(
            (
                ModelOption(
                    provider="codex-oauth",
                    model="gpt-test",
                    reasoning_efforts=("low",),
                    default_effort="low",
                    capability_source="controlled",
                ),
            ),
            ModelSelection(provider="codex-oauth", model="gpt-test", reasoning_effort="low"),
        )
        self.marks: list[tuple[str, str, str, str | None]] = []

    def record_execution(
        self, provider: str, model: str, state: str, reason_code: str | None
    ) -> None:
        self.marks.append((provider, model, state, reason_code))


class Refusing(ControlledResearchModel):
    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        raise ModelExecutionHold(self.reason)


async def run(tmp_path: Path, model: ControlledResearchModel) -> RecordingCatalog:
    catalog = RecordingCatalog()
    runtime = create_runtime(tmp_path, model_resolver=model, model_catalog=catalog)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {"project_id": "p", "name": "Marks", "cutoff_at": "2026-09-13T00:00:00Z"},
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Question", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
    finally:
        runtime.close()
    return catalog


@pytest.mark.parametrize(
    "reason",
    ["OAUTH_MODEL_NOT_AVAILABLE", "OAUTH_MODEL_NOT_SUPPORTED_400", "CLAUDE_CODE_MODEL_UNSUPPORTED"],
)
async def test_a_refusal_of_the_model_or_account_is_recorded_once_and_nothing_is_switched(
    tmp_path: Path, reason: str
) -> None:
    catalog = await run(tmp_path, Refusing(reason))
    assert catalog.marks == [("codex-oauth", "gpt-test", "REJECTED", reason)]
    assert catalog.defaults().model == "gpt-test"


@pytest.mark.parametrize(
    "reason",
    [
        "MODEL_CALL_TIME_BUDGET_EXHAUSTED",
        "OAUTH_AUTH_REQUIRED_401",
        "OAUTH_REQUEST_REJECTED_429",
        "XAI_TRANSPORT_FAILURE",
        "CLAUDE_CODE_LOGIN_REQUIRED",
    ],
)
async def test_network_time_quota_and_login_failures_are_not_marks(
    tmp_path: Path, reason: str
) -> None:
    assert (await run(tmp_path, Refusing(reason))).marks == []


async def test_a_research_that_completes_marks_the_model_verified(tmp_path: Path) -> None:
    catalog = await run(tmp_path, ControlledResearchModel())
    assert catalog.marks == [("codex-oauth", "gpt-test", "VERIFIED", None)]


def test_only_a_refusal_of_the_model_or_account_counts() -> None:
    assert is_model_rejection("OAUTH_MODEL_NOT_AVAILABLE")
    assert is_model_rejection("OAUTH_MODEL_NOT_SUPPORTED_400")
    assert is_model_rejection("CLAUDE_CODE_MODEL_UNSUPPORTED")
    for other in ("OAUTH_REQUEST_REJECTED_400", "XAI_FIRST_BYTE_TIMEOUT_REMOTE_STOP_UNKNOWN", "X"):
        assert not is_model_rejection(other)
    _ = cast(object, None)
