"""The project's cut-off-call retry switch: its RPC and the way an investigation picks it up."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_call_settings import AUTO_RETRY_INTERRUPTED_DEFAULT
from thoth.domain.research_execution import research_work

Json = dict[str, JsonValue]


class SeesTheSwitch(ControlledResearchModel):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[bool] = []

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        work = research_work.get()
        self.seen.append(False if work is None else work.auto_retry_interrupted_call)
        return await super().structured(request)


def test_the_default_is_one_line_and_off() -> None:
    assert AUTO_RETRY_INTERRUPTED_DEFAULT is False


@pytest.mark.asyncio
async def test_the_switch_is_read_and_changed_with_the_digest_it_read(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:

        async def call(method: str, key: str, **more: JsonValue) -> Json:
            return value(
                await runtime.bus.dispatch(request(method, key, {"project_id": "p", **more}))
            )

        first = await call("model/callSettings/read", "r1")
        assert first == {
            "auto_retry_interrupted_model_call": False,
            "hypothesis_contract_v3": False,
            "settings_digest": None,
        }
        on = await call(
            "model/callSettings/update",
            "on",
            auto_retry_interrupted_model_call=True,
            expected_digest=None,
        )
        assert on["auto_retry_interrupted_model_call"] is True and on["settings_digest"]
        stale = await runtime.bus.dispatch(
            request(
                "model/callSettings/update",
                "stale",
                {"project_id": "p", "auto_retry_interrupted_model_call": False},
            )
        )
        assert (
            stale.error is not None
            and "MODEL_CALL_SETTINGS_REVISION_CONFLICT" in stale.error.message
        )
        assert (await call("model/callSettings/read", "r2"))[
            "auto_retry_interrupted_model_call"
        ] is True
        off = await call(
            "model/callSettings/update",
            "off",
            auto_retry_interrupted_model_call=False,
            expected_digest=cast(str, on["settings_digest"]),
        )
        assert off["auto_retry_interrupted_model_call"] is False
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_investigation_sees_the_switch_only_when_the_project_turned_it_on(
    tmp_path: Path,
) -> None:
    model = SeesTheSwitch()
    runtime = await setup(tmp_path, model, source=False)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start-off",
                    {"project_id": "p", "problem": "왜 지연이 생기나요?", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        assert model.seen and not any(model.seen)
        value(
            await runtime.bus.dispatch(
                request(
                    "model/callSettings/update",
                    "turn-on",
                    {
                        "project_id": "p",
                        "auto_retry_interrupted_model_call": True,
                        "expected_digest": None,
                    },
                )
            )
        )
        model.seen.clear()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "input-on",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "contract_version": 2,
                        "instruction": "조건 alpha로 한정",
                    },
                )
            )
        )
        await runtime.bus.drain()
        assert model.seen and all(model.seen)
    finally:
        runtime.close()
