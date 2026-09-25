import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from openai import DEFAULT_TIMEOUT

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.domain.model_dispatch import OAuthSession
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.ports.model import ModelExecutionHold, ModelTransportCancelled, ModelTransportHold


class Session:
    def read(self) -> OAuthSession:
        return OAuthSession("test-token", "test-account", "new-default", "low")


@pytest.mark.asyncio
async def test_frozen_effort_reaches_tool_free_wire_and_byte_limit_closes_transport():
    calls: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        event = {
            "type": "response.completed",
            "response": {
                "id": "fixture",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}
                ],
            },
        }
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\r\n\r\n")

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
    settings = ResolvedModelSettings(
        provider="codex-oauth",
        model="selected-model",
        reasoning_effort="high",
        source_by_field={},
        capability_source="fixture",
        settings_digest="a" * 64,
    )
    prepared = executor.prepare(
        "test", {"type": "object"}, output_tokens=100, timeout_seconds=4, model_settings=settings
    )
    long_call = executor.prepare("test", {"type": "object"}, output_tokens=100, timeout_seconds=650)
    assert long_call.timeout_seconds == 650
    reply = await executor.dispatch(prepared)
    wire = json.loads(calls[0])
    assert wire["model"] == "selected-model" and wire["reasoning"]["effort"] == "high"
    assert wire["tools"] == [] and wire["tool_choice"] == "none"
    assert "max_output_tokens" not in wire
    assert reply.input_tokens is None and reply.output_tokens is None
    with pytest.raises(ModelExecutionHold, match="VISIBLE_BYTE_LIMIT"):
        await executor.dispatch(replace(prepared, max_visible_output_bytes=3))


async def test_no_overall_research_deadline_preserves_user_cancellation():
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"] == DEFAULT_TIMEOUT.as_dict()
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200)

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
    prepared = executor.prepare("test", {"type": "object"}, output_tokens=100, timeout_seconds=None)
    task = asyncio.create_task(executor.dispatch(prepared))
    await started.wait()
    task.cancel()
    with pytest.raises(ModelTransportCancelled) as failure:
        await task
    observation = failure.value.observation
    assert observation.timeout_ms is None
    assert observation.local_cancel_requested and observation.transport_closed


async def test_observed_output_crosses_retired_visible_and_total_stream_limits():
    text = "evidence " * 5000
    progress = (
        b"data: "
        + json.dumps({"type": "response.in_progress", "padding": "x" * 900000}).encode()
        + b"\n\n"
    )
    completed = (
        b"data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "fixture",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": text}]}
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 200},
                },
            }
        ).encode()
        + b"\n\n"
    )
    executor = CodexHttpExecutor(
        Session(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=progress * 5 + completed)
        ),
    )
    prepared = executor.prepare(
        "fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None
    )
    assert prepared.max_visible_output_bytes is None and prepared.max_stream_bytes is None
    assert prepared.capability.output_control == "OBSERVATION_ONLY"
    reply = await executor.dispatch(prepared)
    assert reply.text == text and len(text.encode()) > 24000
    assert reply.received_bytes > 4 * 1024 * 1024
    assert reply.input_tokens == 100 and reply.output_tokens == 200
    assert reply.observation and reply.observation.transport_closed


@pytest.mark.parametrize("terminated", [False, True])
async def test_single_oversized_sse_frame_is_rejected_without_a_total_stream_cap(terminated: bool):
    content = b'data: {"type":"response.in_progress","padding":"' + b"x" * 300 + b'"}'
    if terminated:
        content += b"\n\n"
    executor = CodexHttpExecutor(
        Session(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    prepared = replace(
        executor.prepare("fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None),
        max_frame_bytes=128,
    )
    with pytest.raises(ModelTransportHold, match="OAUTH_SSE_FRAME_TOO_LARGE") as caught:
        await executor.dispatch(prepared)
    assert caught.value.observation.transport_closed
