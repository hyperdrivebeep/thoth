import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from tests.unit.models.test_oauth_http_rejection import CountingBoundary, completed_event
from tests.unit.models.test_oauth_receive_observation import model_request

from thoth.adapters.models.xai_catalog import OmoXaiModelCatalog
from thoth.adapters.models.xai_model import XaiOAuthModel
from thoth.adapters.models.xai_oauth import (
    OmoXaiSessionReader,
    XaiSessionUnavailable,
    xai_model_entries,
)
from thoth.adapters.models.xai_responses import XaiResponsesExecutor
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelTransportHold


def _isolate_local_xai_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from thoth.adapters.models import local_credentials
    from thoth.adapters.models.local_credentials import LocalCredentialHold

    def unavailable(_provider: str, _root: Path | None = None) -> str:
        raise LocalCredentialHold("TEST_LOCAL_SECRET_UNAVAILABLE")

    monkeypatch.setattr(local_credentials, "local_secret", unavailable)


def _write_xai_home(root: Path) -> None:
    agent = root / "agent"
    agent.mkdir()
    (agent / "auth.json").write_text(
        json.dumps({"xai": {"type": "oauth", "access": "xai-secret-token", "expires": 1}}),
        encoding="utf-8",
    )
    (agent / "models-store.json").write_text(
        json.dumps(
            {
                "xai": {
                    "models": [
                        {
                            "id": "grok-4.6",
                            "api": "openai-responses",
                            "baseUrl": "https://api.x.ai/v1",
                            "thinkingLevelMap": {
                                "low": "low",
                                "medium": "medium",
                                "high": "high",
                                "xhigh": "xhigh",
                            },
                        },
                        {
                            "id": "grok-4.5",
                            "api": "openai-responses",
                            "baseUrl": "https://api.x.ai/v1",
                            "thinkingLevelMap": {"low": "low", "medium": "medium", "high": "high"},
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _settings(model: str = "grok-4.6", effort: str | None = "high") -> ResolvedModelSettings:
    return ResolvedModelSettings(
        provider="xai",
        model=model,
        reasoning_effort=effort,
        source_by_field={"provider": "REQUEST", "model": "REQUEST", "reasoning_effort": "REQUEST"},
        capability_source="omo-xai-models-store/openai-responses-v1",
        settings_digest="b" * 64,
    )


def test_catalog_advertises_verified_efforts_only(tmp_path: Path) -> None:
    _write_xai_home(tmp_path)
    options = {item.model: item for item in OmoXaiModelCatalog(tmp_path / "agent").options()}
    assert options["grok-4.6"].reasoning_efforts == ("low", "medium", "high", "xhigh")
    assert options["grok-4.5"].reasoning_efforts == ("low", "medium", "high")
    assert "xhigh" not in options["grok-4.5"].reasoning_efforts


def test_malformed_xai_store_items_do_not_advertise_models(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models-store.json").write_text(
        json.dumps(
            {
                "xai": {
                    "models": [
                        None,
                        {"id": 7, "api": "openai-responses", "baseUrl": "https://api.x.ai/v1"},
                        {
                            "id": "untrusted-endpoint",
                            "api": "openai-responses",
                            "baseUrl": "https://other.example/v1",
                        },
                        {
                            "id": "grok-4.6",
                            "api": "openai-responses",
                            "baseUrl": "https://api.x.ai/v1",
                            "thinkingLevelMap": {"high": "high", "unsafe": "unsafe"},
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert xai_model_entries(agent) == ({"id": "grok-4.6", "efforts": ("high",)},)


def test_xai_session_prefers_local_credential_over_omo_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.models import local_credentials

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "auth.json").write_text("not-json", encoding="utf-8")

    def local_secret(_provider: str, _root: Path | None = None) -> str:
        return "local-test-token"

    monkeypatch.setattr(local_credentials, "local_secret", local_secret)
    session = OmoXaiSessionReader(agent, model="grok-4.6").read()
    assert session.access_token == "local-test-token"


def test_omo_compatibility_session_requires_explicit_root() -> None:
    with pytest.raises(XaiSessionUnavailable, match="XAI_CURRENT_SESSION_UNAVAILABLE"):
        OmoXaiSessionReader(model="grok-4.6").read()


@pytest.mark.asyncio
async def test_xai_success_uses_omo_native_responses_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_local_xai_secret(monkeypatch)
    _write_xai_home(tmp_path)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=completed_event())

    executor = XaiResponsesExecutor(
        OmoXaiSessionReader(tmp_path / "agent", model="grok-4.6"),
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
async def test_xai_401_and_429_hold_without_retry_or_codex_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_local_xai_secret(monkeypatch)
    _write_xai_home(tmp_path)
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
        OmoXaiSessionReader(tmp_path / "agent", model="grok-4.6"),
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
