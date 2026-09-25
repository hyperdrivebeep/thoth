import asyncio
import json
from typing import cast
from unittest.mock import MagicMock

import httpx
import pytest
from tests.unit.memory.test_codex_memory_reviewer import FakeExecutor
from tests.unit.models.test_oauth_wire_controls import Session
from tests.unit.models.test_transport_diagnostic import (
    DiagnosticSession,
    FailingStream,
    failure_response,
)
from tests.unit.test_post_audit_contracts import ref

from thoth.adapters.memory import CodexOAuthMemoryReviewer
from thoth.adapters.memory.bounded_review import bounded_review
from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.domain.memory import MemoryReviewContext, MemoryReviewRole
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_execution import ResearchBoundary, ResearchWork, research_work
from thoth.ports.model import ModelExecutionHold


def context() -> MemoryReviewContext:
    return MemoryReviewContext(
        role=MemoryReviewRole.FACTS,
        candidate_digest="a" * 64,
        fields={"unsafe": False, "missing": False, "conflict": False},
        context_digest="b" * 64,
    )


@pytest.mark.asyncio
async def test_memory_review_uses_frozen_settings_and_final_wire_reservation() -> None:
    payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        event = {
            "type": "response.completed",
            "response": {
                "id": "memory-fixture",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"verdict":"PASS","reason_code":"CONTEXT_PASS"}',
                            }
                        ],
                    }
                ],
            },
        }
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

    boundary = MagicMock(spec=ResearchBoundary)
    boundary.call_timeout.return_value = 3
    boundary.new_model_call.return_value = "memory-call"
    work = ResearchWork(ref(), "memory review", cast(ResearchBoundary, boundary))
    work.model_settings = ResolvedModelSettings(
        provider="codex-oauth",
        model="frozen-model",
        reasoning_effort="high",
        source_by_field={},
        capability_source="fixture",
        settings_digest="a" * 64,
    )
    token = research_work.set(work)
    try:
        reviewer = CodexOAuthMemoryReviewer(
            CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
        )
        result = await reviewer.review(context())
        assert result.reason_code == "CONTEXT_PASS"
        wire = json.loads(payloads[0])
        assert wire["model"] == "frozen-model" and wire["reasoning"]["effort"] == "high"
        assert wire["tools"] == []
        assert boundary.reserve_dispatch.call_args.args[1] == payloads[0]
        assert boundary.record_usage.call_count == 1
        legacy = FakeExecutor('{"verdict":"PASS","reason_code":"CONTEXT_PASS"}')
        with pytest.raises(ModelExecutionHold, match="BOUNDED_TRANSPORT_REQUIRED"):
            await CodexOAuthMemoryReviewer(legacy).review(context())
        assert not legacy.prompts
    finally:
        research_work.reset(token)


@pytest.mark.parametrize("cancel", [False, True])
async def test_memory_hold_and_cancel_settle_once_with_response_id_and_diagnostic(cancel: bool):
    stream = FailingStream(None if cancel else httpx.ReadError("fixture"))
    executor = CodexHttpExecutor(
        DiagnosticSession(), transport=httpx.MockTransport(lambda _: failure_response(stream))
    )
    boundary = MagicMock(spec=ResearchBoundary)
    boundary.call_timeout.return_value = None
    boundary.new_model_call.return_value = "memory-diagnostic"
    token = research_work.set(ResearchWork(ref(), "memory", cast(ResearchBoundary, boundary)))
    try:
        task = asyncio.create_task(bounded_review(executor, "fixture", {"type": "object"}))
        await stream.started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else ModelExecutionHold):
            await task
        assert boundary.reserve_dispatch.call_count == 1 and boundary.record_usage.call_count == 1
        reserved_id = boundary.reserve_dispatch.call_args.args[0]
        recorded = boundary.record_usage.call_args
        assert recorded.args[0] == reserved_id and recorded.args[5] == "diagnostic-response"
        observation = recorded.kwargs["observation"]
        assert observation.transport_diagnostic is not None
        assert observation.transport_diagnostic.httpx_error_type == (
            None if cancel else "ReadError"
        )
        assert stream.close_count == 1
    finally:
        research_work.reset(token)
