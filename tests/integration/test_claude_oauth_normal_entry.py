"""Normal THOTH model entry consumes a synthetic Claude Messages route and stores results."""

import json
import time
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import BaseModel
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.models.claude_catalog import ClaudeOAuthCatalog
from thoth.adapters.models.claude_messages import CLAUDE_MESSAGES_CONTROL, ClaudeMessagesModel
from thoth.adapters.models.claude_oauth import ClaudeOAuthBroker
from thoth.adapters.models.claude_profile import ClaudeCredential
from thoth.apps.runtime import create_runtime
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability
from thoth.ports.model import ModelPort

pytestmark = pytest.mark.usefixtures("xai_http_guard")
TModel = TypeVar("TModel", bound=BaseModel)
MODEL = "claude-sonnet-4-5-20250929"


class FakeClaudeRoute(ModelPort):
    def __init__(self, broker: ClaudeOAuthBroker) -> None:
        self.broker = broker
        self.oracle = ControlledResearchModel()
        self.sent: list[dict[str, object]] = []

    @property
    def control_capability(self) -> ModelControlCapability:
        return CLAUDE_MESSAGES_CONTROL

    def resolve(self, *, provider: str, model: str | None = None) -> ModelPort:
        assert provider == "claude-oauth" and model == MODEL
        return self

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        oracle = await self.oracle.structured(request)
        output = oracle.output.model_dump_json()

        def reply(wire: httpx.Request) -> httpx.Response:
            assert str(wire.url) == "https://api.anthropic.com/v1/messages"
            assert wire.headers["Authorization"] == "Bearer synthetic-access"
            body: dict[str, object] = json.loads(wire.content)
            assert "tools" not in body and "tool_choice" not in body
            assert "thinking" not in body
            output_config = body["output_config"]
            assert isinstance(output_config, dict)
            assert "effort" not in output_config
            self.sent.append(body)
            return httpx.Response(
                200,
                json={
                    "type": "message",
                    "id": f"msg_synthetic_{len(self.sent)}",
                    "model": MODEL,
                    "content": [{"type": "text", "text": output}],
                    "usage": {"input_tokens": 4, "output_tokens": 4},
                    "stop_reason": "end_turn",
                },
            )

        return await ClaudeMessagesModel(
            self.broker, model=MODEL, transport=httpx.MockTransport(reply)
        ).structured(request)


@pytest.mark.asyncio
async def test_claude_selected_route_fake_messages_persists_after_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "isolated-home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME"):
        monkeypatch.setenv(name, str(home / name.lower()))
    monkeypatch.setenv("THOTH_CODEX_PACKAGE_ROOT", str(home / "missing-codex-package"))

    workspace = tmp_path / "workspace"

    def no_auth_calls(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("fresh synthetic credential must not contact the auth endpoint")

    broker = ClaudeOAuthBroker(
        workspace, client_id="synthetic-client", transport=httpx.MockTransport(no_auth_calls)
    )
    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "synthetic-access",
                "synthetic-refresh",
                time.time() + 3600,
                "generation-synthetic",
                broker.profile.profile_id,
            )
        )
    route = FakeClaudeRoute(broker)
    catalog = ClaudeOAuthCatalog(workspace, broker=broker)
    runtime = create_runtime(
        workspace,
        model_resolver=route,
        model_catalog=catalog,
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "claude-project",
                    {
                        "project_id": "project:claude",
                        "name": "Claude synthetic",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        unsupported_effort = await runtime.bus.dispatch(
            request(
                "model/settings/update",
                "claude-unsupported-effort",
                {
                    "project_id": "project:claude",
                    "expected_digest": None,
                    "selection": {
                        "provider": "claude-oauth",
                        "model": MODEL,
                        "reasoning_effort": "high",
                    },
                },
            )
        )
        assert unsupported_effort.error is not None
        assert unsupported_effort.error.message == "MODEL_REASONING_EFFORT_UNSUPPORTED"
        assert route.sent == []
        selected = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "claude-select",
                    {
                        "project_id": "project:claude",
                        "expected_digest": None,
                        "selection": {"provider": "claude-oauth", "model": MODEL},
                    },
                )
            )
        )
        assert selected["availability"] == "AVAILABLE"
        assert selected["effective_settings"]["reasoning_effort"] is None
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "claude-start",
                    {
                        "project_id": "project:claude",
                        "problem": "Why is latency uncertain?",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(admitted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        assert route.sent
        assert all(body["model"] == MODEL for body in route.sent)
        for body in route.sent:
            output_limit = body.get("max_tokens")
            assert isinstance(output_limit, int) and output_limit > 0
        current = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "claude-current",
                    {
                        "project_id": "project:claude",
                        "thread_id": admitted["thread_id"],
                    },
                )
            )
        )
        assert current["current_result"]["completion"] == "TERMINAL"
        assert "synthetic-access" not in json.dumps(current)
    finally:
        runtime.close()
    reopened = create_runtime(
        workspace,
        model_resolver=route,
        model_catalog=catalog,
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        readback = value(
            await reopened.bus.query(
                request(
                    "thread/read",
                    "claude-reopen",
                    {
                        "project_id": "project:claude",
                        "thread_id": admitted["thread_id"],
                    },
                )
            )
        )
        assert readback["current_result"]["result"] == current["current_result"]["result"]
    finally:
        reopened.close()
