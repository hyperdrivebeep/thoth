import json
from dataclasses import replace

import httpx
import pytest
from tests.unit.models.test_oauth_http_rejection import CountingBoundary, completed_event
from tests.unit.models.test_oauth_receive_observation import model_request

from thoth.adapters.models.xai_model import XaiOAuthModel
from thoth.adapters.models.xai_oauth import XaiSession
from thoth.adapters.models.xai_responses import XaiResponsesExecutor
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelTransportHold

pytestmark = pytest.mark.usefixtures("xai_http_guard")


class _FixtureSessionReader:
    def read(self) -> XaiSession:
        return XaiSession("xai-secret-token", "grok-4.6")


def _settings(model: str = "grok-4.6", effort: str | None = "high") -> ResolvedModelSettings:
    return ResolvedModelSettings(
        provider="xai",
        model=model,
        reasoning_effort=effort,
        source_by_field={"provider": "REQUEST", "model": "REQUEST", "reasoning_effort": "REQUEST"},
        capability_source="fixture-xai/openai-responses-v1",
        settings_digest="b" * 64,
    )


@pytest.mark.asyncio
async def test_xai_success_uses_responses_payload() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=completed_event())

    executor = XaiResponsesExecutor(
        _FixtureSessionReader(),
        transport=httpx.MockTransport(handler),
    )
    prepared = executor.prepare(
        "ping",
        {"type": "object"},
        output_tokens=100,
        timeout_seconds=4,
        model_settings=_settings(),
    )
    reply = await executor.dispatch(prepared)
    assert reply.text == '{"label":"ok"}'
    request = calls[0]
    assert str(request.url) == "https://api.x.ai/v1/responses"
    assert request.headers["Authorization"] == "Bearer xai-secret-token"
    assert "ChatGPT-Account-Id" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "grok-4.6"
    assert body["reasoning"]["effort"] == "high"
    assert body["store"] is False
    assert body["stream"] is True
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "parallel_tool_calls" not in body
    assert "instructions" not in body
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["schema"] == {"type": "object"}
    assert "max_output_tokens" not in body
    dumped = json.dumps(reply.observation.model_dump(mode="json") if reply.observation else {})
    assert "xai-secret-token" not in dumped


@pytest.mark.asyncio
async def test_xai_401_and_429_hold_without_retry_or_codex_switch() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(401, content=b'{"error":{"code":"invalid_api_key"}}')
        return httpx.Response(
            429,
            content=b'{"error":{"code":"rate_limit_exceeded"}}',
            headers={"retry-after": "0"},
        )

    executor = XaiResponsesExecutor(
        _FixtureSessionReader(),
        transport=httpx.MockTransport(handler),
    )
    work = ResearchWork(
        RevisionRef(
            project_id="test",
            entity_type="THREAD",
            entity_id="request:t",
            revision_id="revision:r",
            revision_digest="0" * 64,
            schema_version="2.0.0",
        ),
        "Ping",
        CountingBoundary(),
    )
    from thoth.domain.oauth_retry import OAuthRetryPolicy

    work.oauth_retry_policy = OAuthRetryPolicy()
    token = research_work.set(work)
    try:
        with pytest.raises(ModelTransportHold, match="XAI_AUTH_REQUIRED_401"):
            await XaiOAuthModel(executor, max_repair_attempts=0).structured(
                replace(model_request(), model_settings=_settings())
            )
        with pytest.raises(ModelTransportHold, match="XAI_REQUEST_REJECTED_429"):
            await XaiOAuthModel(executor, max_repair_attempts=0).structured(
                replace(model_request(), model_settings=_settings())
            )
        assert calls == 2
    finally:
        research_work.reset(token)
