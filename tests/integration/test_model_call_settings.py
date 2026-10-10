"""The project's cut-off-call retry switch: its RPC and the way an investigation picks it up."""

from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import BaseModel, JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.unit.models.test_transport_diagnostic import (
    DiagnosticSession,
    FailingStream,
    failure_response,
)

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.apps.runtime import create_runtime
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_call_settings import AUTO_RETRY_INTERRUPTED_DEFAULT
from thoth.domain.model_settings import ModelOption, ModelSelection
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


def test_the_default_is_one_line_and_on() -> None:
    # 사용자 결정 20261010-01: the default changed; explicit saved False remains valid.
    assert AUTO_RETRY_INTERRUPTED_DEFAULT is True


@pytest.mark.asyncio
async def test_the_switch_is_read_and_changed_with_the_digest_it_read(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:

        async def call(method: str, key: str, **more: JsonValue) -> Json:
            return value(
                await runtime.bus.dispatch(request(method, key, {"project_id": "p", **more}))
            )

        first = await call("model/callSettings/read", "r1")
        # 사용자 결정 20261010-01: an unsaved project starts on.
        assert first == {
            "auto_retry_interrupted_model_call": True,
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


@pytest.mark.parametrize("saved", [None, False, True])
async def test_saved_retry_choice_survives_reopen_and_controls_actual_extra_sends(
    tmp_path: Path,
    saved: bool | None,
) -> None:
    # 사용자 결정 20261010-01: only an absent choice defaults on; saved False means one send.
    initial = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        if saved is not None:
            value(
                await initial.bus.dispatch(
                    request(
                        "model/callSettings/update",
                        "save",
                        {
                            "project_id": "p",
                            "auto_retry_interrupted_model_call": saved,
                            "expected_digest": None,
                        },
                    )
                )
            )
    finally:
        initial.close()

    sends: list[httpx.Request] = []
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    def respond(req: httpx.Request) -> httpx.Response:
        sends.append(req)
        return failure_response(FailingStream(httpx.RemoteProtocolError("peer closed connection")))

    executor = CodexHttpExecutor(DiagnosticSession(), transport=httpx.MockTransport(respond))
    resolver = RegisteredModelResolver()
    resolver.register(
        "codex-oauth", lambda _: CodexOAuthModel(executor, sleep=sleep, jitter=lambda: 0.5)
    )
    catalog = StaticModelCatalog(
        (
            ModelOption(
                provider="codex-oauth",
                model="diagnostic-fixture",
                reasoning_efforts=("high",),
                default_effort="high",
                capability_source="codex-test",
            ),
        ),
        ModelSelection(provider="codex-oauth", model="diagnostic-fixture", reasoning_effort="high"),
    )
    runtime = create_runtime(tmp_path, model_resolver=resolver, model_catalog=catalog)
    try:
        settings = value(
            await runtime.bus.dispatch(
                request("model/callSettings/read", "read", {"project_id": "p"})
            )
        )
        assert settings["auto_retry_interrupted_model_call"] is (True if saved is None else saved)
        assert (settings["settings_digest"] is None) is (saved is None)
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Inspect connected records",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "result",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                    },
                )
            )
        )
        expected_sends = 1 if saved is False else 3
        assert len(sends) == expected_sends
        assert waits == ([] if saved is False else [2.0, 4.0])
        dispatches = state["model_dispatches"]
        assert isinstance(dispatches, list) and len(dispatches) == expected_sends
        result = state["current_result"]
        assert isinstance(result, dict) and result["terminal_reason"] == "OAUTH_TRANSPORT_FAILURE"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_investigation_sees_default_on_then_the_projects_saved_off_choice(
    tmp_path: Path,
) -> None:
    model = SeesTheSwitch()
    runtime = await setup(tmp_path, model, source=False)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start-on",
                    {"project_id": "p", "problem": "왜 지연이 생기나요?", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        # 사용자 결정 20261010-01: normal entry consumes the new unsaved default.
        assert model.seen and all(model.seen)
        value(
            await runtime.bus.dispatch(
                request(
                    "model/callSettings/update",
                    "turn-off",
                    {
                        "project_id": "p",
                        "auto_retry_interrupted_model_call": False,
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
                    "input-off",
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
        # 사용자 결정 20261010-01: explicitly saved False overrides the default.
        assert model.seen and not any(model.seen)
    finally:
        runtime.close()
