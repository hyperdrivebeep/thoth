"""Synthetic Claude Messages wire, dispatch budget and generation fencing."""

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from tests.unit.models.test_oauth_http_rejection import CountingBoundary
from tests.unit.models.test_oauth_receive_observation import model_request

from thoth.adapters.models.claude_messages import ClaudeMessagesHold, ClaudeMessagesModel
from thoth.adapters.models.claude_oauth import ClaudeOAuthBroker, ClaudeOAuthHold
from thoth.adapters.models.claude_profile import ClaudeCredential
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef

pytestmark = pytest.mark.usefixtures("xai_http_guard")
MODEL = "claude-sonnet-4-5-20250929"


class BoundedBoundary(CountingBoundary):
    def call_timeout(self) -> float:
        return 5.0


class OneSecondBoundary(CountingBoundary):
    def call_timeout(self) -> float:
        return 1.0


def test_standard_messages_transport_can_be_configured_without_calling_it(
    tmp_path: Path,
) -> None:
    broker = ClaudeOAuthBroker(tmp_path, client_id="synthetic-client")
    model = ClaudeMessagesModel(broker, model=MODEL)
    assert model.transport is None
    assert model.endpoint == "https://api.anthropic.com/v1/messages"


def ready_broker(tmp_path: Path) -> ClaudeOAuthBroker:
    broker = ClaudeOAuthBroker(tmp_path, client_id="synthetic-client")
    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "synthetic-access",
                "synthetic-refresh",
                time.time() + 3600,
                "generation-1",
                broker.profile.profile_id,
            )
        )
    return broker


def settings(effort: str | None = None) -> ResolvedModelSettings:
    return ResolvedModelSettings(
        provider="claude-oauth",
        model=MODEL,
        reasoning_effort=effort,
        source_by_field={"provider": "REQUEST", "model": "REQUEST", "reasoning_effort": "REQUEST"},
        capability_source="thoth-curated/senpi-ai-2026.9.26-anthropic-json",
        settings_digest="b" * 64,
    )


def reply(
    *, status: int = 200, text: str = '{"label":"ok"}', block: str = "text"
) -> httpx.Response:
    if status != 200:
        return httpx.Response(status, json={"type": "error", "error": {"type": "synthetic"}})
    return httpx.Response(
        200,
        json={
            "type": "message",
            "id": "msg_synthetic",
            "model": MODEL,
            "content": [{"type": block, "text": text}] if block == "text" else [{"type": block}],
            "usage": {"input_tokens": 4, "output_tokens": 2},
            "stop_reason": "end_turn",
        },
    )


@pytest.mark.asyncio
async def test_one_explicit_messages_post_has_schema_effort_tools_zero_and_budget(
    tmp_path: Path,
) -> None:
    broker = ready_broker(tmp_path)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply()

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    boundary = BoundedBoundary()
    work = ResearchWork(
        RevisionRef(
            project_id="test",
            entity_type="THREAD",
            entity_id="request:synthetic",
            revision_id="revision:synthetic",
            revision_digest="0" * 64,
            schema_version="2.0.0",
        ),
        "Synthetic question",
        boundary,
    )
    token = research_work.set(work)
    try:
        result = await model.structured(replace(model_request(), model_settings=settings()))
    finally:
        research_work.reset(token)
    assert result.output.label == "ok"
    assert result.model_id == f"claude-oauth/{MODEL}"
    assert len(sent) == 1 and boundary.reservations == 1
    assert len(result.dispatch_ids) == 1
    assert boundary.response_ids == ["msg_synthetic"]
    assert boundary.remote_stops == ["end_turn"]
    assert str(sent[0].url) == "https://api.anthropic.com/v1/messages"
    assert sent[0].headers["Authorization"] == "Bearer synthetic-access"
    assert sent[0].headers["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in sent[0].headers
    assert "x-app" not in sent[0].headers
    body = json.loads(sent[0].content)
    assert body["model"] == MODEL
    assert body["max_tokens"] == model_request().max_output_tokens
    assert "effort" not in body["output_config"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body
    assert "tools" not in body and "tool_choice" not in body
    assert "synthetic-access" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "block", "text", "reason"),
    (
        (401, "text", "", "CLAUDE_AUTH_REQUIRED_401"),
        (403, "text", "", "CLAUDE_AUTH_REQUIRED_403"),
        (429, "text", "", "CLAUDE_RATE_LIMIT_429"),
        (200, "tool_use", "", "CLAUDE_NATIVE_TOOL_OR_BLOCK_UNSUPPORTED"),
        (200, "text", "not-json", "CLAUDE_STRUCTURED_OUTPUT_INVALID"),
    ),
)
async def test_rejection_tool_and_bad_schema_never_retry(
    tmp_path: Path, status: int, block: str, text: str, reason: str
) -> None:
    broker = ready_broker(tmp_path)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply(status=status, text=text, block=block)

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    with pytest.raises(ClaudeMessagesHold, match=reason):
        await model.structured(replace(model_request(), model_settings=settings(None)))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_generation_change_after_snapshot_sends_zero_post(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply()

    original = broker.execution_session

    def changed():
        snapshot = original()
        with broker.profile.lock():
            broker.profile.save(
                ClaudeCredential(
                    "new-access",
                    "new-refresh",
                    time.time() + 3600,
                    "generation-2",
                    broker.profile.profile_id,
                )
            )
        return snapshot

    broker.execution_session = changed  # type: ignore[method-assign]
    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    with pytest.raises(ClaudeOAuthHold, match="CLAUDE_AUTH_SNAPSHOT_CHANGED"):
        await model.structured(replace(model_request(), model_settings=settings(None)))
    assert calls == []


@pytest.mark.asyncio
async def test_unconfirmed_sonnet_45_effort_is_rejected_before_post(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply()

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    with pytest.raises(ClaudeMessagesHold, match="CLAUDE_EFFORT_UNSUPPORTED"):
        await model.structured(replace(model_request(), model_settings=settings("high")))
    assert calls == []


@pytest.mark.asyncio
async def test_cancellation_closes_local_transport_without_duplicate_send(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    entered = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.Event().wait()
        return reply()

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    task = asyncio.create_task(
        model.structured(replace(model_request(), model_settings=settings()))
    )
    await asyncio.wait_for(entered.wait(), 4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_messages_keep_event_loop_heartbeat_live(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    first_entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await release.wait()
        return reply()

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    selected = replace(model_request(), model_settings=settings(None))
    first = asyncio.create_task(model.structured(selected))
    try:
        await asyncio.wait_for(first_entered.wait(), 4)
        second = asyncio.create_task(model.structured(selected))
        ticks = 0
        for _ in range(12):
            await asyncio.sleep(0.015)
            ticks += 1
        assert ticks == 12 and not second.done()
        release.set()
        one, two = await asyncio.wait_for(asyncio.gather(first, second), 5)
        assert one.output.label == two.output.label == "ok"
        assert calls == 2
    finally:
        release.set()
        if not first.done():
            first.cancel()


@pytest.mark.asyncio
async def test_cancelled_async_lock_waiter_leaves_no_late_owner(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    session = broker.execution_session()
    entered = asyncio.Event()

    async def wait_for_gate() -> None:
        async with broker.dispatch_gate_async(session, timeout_seconds=2):
            entered.set()

    async with broker.dispatch_gate_async(session):
        waiting = asyncio.create_task(wait_for_gate())
        await asyncio.sleep(0.06)
        assert not entered.is_set()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    with broker.profile.lock(timeout_seconds=1):
        pass
    await asyncio.sleep(0.1)
    with broker.profile.lock(timeout_seconds=1):
        pass
    assert not entered.is_set()


@pytest.mark.asyncio
async def test_auth_preparation_consumes_whole_call_deadline_without_model_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = ready_broker(tmp_path)
    calls: list[httpx.Request] = []
    original = broker.execution_session

    def slow_session():
        time.sleep(0.22)
        return original()

    monkeypatch.setattr(broker, "execution_session", slow_session)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply()

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    boundary = CountingBoundary()
    work = ResearchWork(
        RevisionRef(
            project_id="test",
            entity_type="THREAD",
            entity_id="request:deadline",
            revision_id="revision:deadline",
            revision_digest="0" * 64,
            schema_version="2.0.0",
        ),
        "Synthetic deadline",
        boundary,
    )
    token = research_work.set(work)
    try:
        with pytest.raises(ClaudeMessagesHold, match="CLAUDE_AUTH_PREPARATION_DEADLINE"):
            await model.structured(replace(model_request(), model_settings=settings(None)))
    finally:
        research_work.reset(token)
    assert calls == [] and boundary.reservations == 0


@pytest.mark.asyncio
async def test_ambiguous_transport_after_one_post_never_retries(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("synthetic response lost after request")

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    with pytest.raises(ClaudeMessagesHold, match="CLAUDE_TRANSPORT_OUTCOME_UNKNOWN"):
        await model.structured(replace(model_request(), model_settings=settings()))
    assert calls == 1


@pytest.mark.asyncio
async def test_first_header_deadline_stops_after_one_send(tmp_path: Path) -> None:
    broker = ready_broker(tmp_path)
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return reply()

    model = ClaudeMessagesModel(broker, model=MODEL, transport=httpx.MockTransport(handler))
    boundary = OneSecondBoundary()
    work = ResearchWork(
        RevisionRef(
            project_id="test",
            entity_type="THREAD",
            entity_id="request:header-deadline",
            revision_id="revision:header-deadline",
            revision_digest="0" * 64,
            schema_version="2.0.0",
        ),
        "Synthetic header deadline",
        boundary,
    )
    token = research_work.set(work)
    try:
        with pytest.raises(
            ClaudeMessagesHold, match="CLAUDE_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN"
        ):
            await model.structured(replace(model_request(), model_settings=settings()))
    finally:
        research_work.reset(token)
    assert calls == 1
