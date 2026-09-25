"""HTTP 429 classification and a single approved retry stay separate from SSE failures."""

import json

import httpx
import pytest
from tests.unit.models.test_oauth_receive_observation import Boundary, Session, model_request

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.domain.model_dispatch import (
    ModelControlCapability,
)
from thoth.domain.oauth_retry import OAuthRetryPolicy
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelTransportHold


class CountingBoundary(Boundary):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_ids: list[str] = []

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        self.dispatch_ids.append(dispatch_id)
        super().reserve_dispatch(dispatch_id, payload, output_tokens, capability)


def completed_event() -> bytes:
    return (
        b"data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "ok",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"label":"ok"}'}],
                        }
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            }
        ).encode()
        + b"\n\n"
    )


@pytest.mark.asyncio
async def test_unknown_429_holds_after_one_transport_and_keeps_tokens_unreported() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            content=b'{"error":{"message":"AUTH_CANARY too many"}}',
            headers={"content-type": "application/json"},
        )

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
    boundary = CountingBoundary()
    token = research_work.set(
        ResearchWork(
            RevisionRef(
                project_id="test",
                entity_type="THREAD",
                entity_id="request:t",
                revision_id="revision:r",
                revision_digest="0" * 64,
                schema_version="2.0.0",
            ),
            "Ping",
            boundary,
        )
    )
    try:
        with pytest.raises(ModelTransportHold, match="OAUTH_REQUEST_REJECTED_429") as caught:
            await CodexOAuthModel(executor, max_repair_attempts=0).structured(model_request())
        observation = caught.value.observation
        assert calls == 1 and boundary.reservations == 1
        assert observation.http_status == 429
        assert observation.http_rejection is not None
        assert observation.http_rejection.rejection_kind == "UNKNOWN"
        assert "AUTH_CANARY" not in str(observation.model_dump())
        assert boundary.remote_stops == ["UNKNOWN"]
        assert boundary.observations[0].received_bytes == 0
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_approved_transient_429_retries_once_with_same_payload() -> None:
    payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        if len(payloads) == 1:
            return httpx.Response(
                429,
                content=b'{"error":{"code":"rate_limit_exceeded"}}',
                headers={"retry-after": "0", "content-type": "application/json"},
            )
        return httpx.Response(200, content=completed_event())

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
    boundary = CountingBoundary()
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
        boundary,
    )
    work.oauth_retry_policy = OAuthRetryPolicy()
    token = research_work.set(work)
    try:
        result = await CodexOAuthModel(executor, max_repair_attempts=0).structured(model_request())
        assert result.output.label == "ok"
        assert len(payloads) == 2 and payloads[0] == payloads[1]
        assert boundary.reservations == 2
        assert len(boundary.dispatch_ids) == 2
        assert boundary.dispatch_ids == ["call:0", "call:1"]
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_second_429_does_not_send_a_third_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            content=b'{"error":{"code":"rate_limit_exceeded"}}',
            headers={"retry-after": "0"},
        )

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
    boundary = CountingBoundary()
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
        boundary,
    )
    work.oauth_retry_policy = OAuthRetryPolicy()
    token = research_work.set(work)
    try:
        with pytest.raises(ModelTransportHold, match="OAUTH_REQUEST_REJECTED_429"):
            await CodexOAuthModel(executor, max_repair_attempts=0).structured(model_request())
        assert calls == 2 and boundary.reservations == 2
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_account_limit_429_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=b'{"error":{"code":"insufficient_quota"}}')

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
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
    work.oauth_retry_policy = OAuthRetryPolicy()
    token = research_work.set(work)
    try:
        with pytest.raises(ModelTransportHold) as caught:
            await CodexOAuthModel(executor, max_repair_attempts=0).structured(model_request())
        assert calls == 1
        assert caught.value.observation.http_rejection is not None
        assert caught.value.observation.http_rejection.rejection_kind == "ACCOUNT_LIMIT"
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_chatgpt_unsupported_model_400_is_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=(
                b'{"detail":"The \'gpt-5.4-mini\' model is not supported '
                b'when using Codex with a ChatGPT account."}'
            ),
        )

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
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
    token = research_work.set(work)
    try:
        with pytest.raises(ModelTransportHold, match="OAUTH_MODEL_NOT_SUPPORTED_400") as caught:
            await CodexOAuthModel(executor, max_repair_attempts=0).structured(model_request())
        assert caught.value.observation.http_rejection is not None
        assert (
            caught.value.observation.http_rejection.classification_basis
            == "code:model_not_supported"
        )
    finally:
        research_work.reset(token)
