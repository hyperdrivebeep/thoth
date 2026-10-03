"""A model call cut off in the middle is sent again, at most twice, with a growing wait (T4, A2)."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from decimal import Decimal

import httpx
import pytest
from tests.unit.models.test_oauth_receive_observation import Boundary, Session, model_request

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.domain.model_dispatch import ModelControlCapability, TransportTimeouts
from thoth.domain.oauth_retry import (
    MAX_RETRIES_PER_CALL,
    OAuthRetryPolicy,
    interrupted_retry_delay_seconds,
    is_interrupted_model_call,
)
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelTransportHold


class RetryBoundary(Boundary):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_ids: list[str] = []
        self.retries: list[str | None] = []

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        self.dispatch_ids.append(dispatch_id)
        super().reserve_dispatch(dispatch_id, payload, output_tokens, capability)

    def record_usage(self, *args: object, **kwargs: object) -> None:
        self.retries.append(kwargs.get("retry_of_dispatch_id"))  # type: ignore[arg-type]
        super().record_usage(*args, **kwargs)  # type: ignore[arg-type]


def _completed() -> bytes:
    done = {
        "id": "ok",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"label":"ok"}'}]}
        ],
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }
    event = {"type": "response.completed", "response": done}
    return b"data: " + json.dumps(event).encode() + b"\n\n"


class _Cut(httpx.AsyncByteStream):
    """A reply that starts and is then closed by the far side in the middle of the stream."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"type":"response.output_text.delta","delta":"x"}\n\n'
        raise httpx.RemoteProtocolError("peer closed connection without a complete message body")


def _cut_response() -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_Cut())


def _work(boundary: RetryBoundary, *, auto: bool, retry_429: bool = False) -> ResearchWork:
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
    work.auto_retry_interrupted_call = auto
    if retry_429:
        work.oauth_retry_policy = OAuthRetryPolicy()
    return work


class Waits:
    """Records the waits between attempts instead of sleeping them."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


async def _run(  # type: ignore[no-untyped-def]
    handler: Callable[[httpx.Request], httpx.Response],
    work: ResearchWork,
    waits: Waits | None = None,
    **executor: object,
):
    model = CodexOAuthModel(
        CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler), **executor),  # type: ignore[arg-type]
        max_repair_attempts=0,
        sleep=Waits() if waits is None else waits,
        jitter=lambda: 0.5,
    )
    token = research_work.set(work)
    try:
        return await model.structured(model_request())
    finally:
        research_work.reset(token)


def test_only_cut_off_slowed_and_never_ending_streams_count_as_interrupted() -> None:
    for reason in (
        "OAUTH_TRANSPORT_FAILURE",
        "OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN",
        "OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN",
        "OAUTH_RUNAWAY_OUTPUT_REMOTE_STOP_UNKNOWN",
    ):
        assert is_interrupted_model_call(reason)
    for reason in (
        "OAUTH_AUTH_REQUIRED_401",
        "OAUTH_REQUEST_REJECTED_429",
        "OAUTH_READ_IDLE_TIMEOUT_REMOTE_STOP_UNKNOWN",
        "OAUTH_INCOMPLETE_RESPONSE",
        "OAUTH_INVALID_SSE",
        "OAUTH_REQUEST_REJECTED_400",
    ):
        assert not is_interrupted_model_call(reason)


def test_the_wait_before_a_retry_is_two_then_four_seconds_shaken_by_a_quarter() -> None:
    assert MAX_RETRIES_PER_CALL == 2
    assert interrupted_retry_delay_seconds(0, 0.0) == 1.5
    assert interrupted_retry_delay_seconds(0, 0.5) == 2.0
    assert interrupted_retry_delay_seconds(0, 1.0) == 2.5
    assert interrupted_retry_delay_seconds(1, 0.0) == 3.0
    assert interrupted_retry_delay_seconds(1, 0.5) == 4.0
    assert interrupted_retry_delay_seconds(1, 1.0) == 5.0


@pytest.mark.asyncio
async def test_switched_off_a_cut_off_call_is_not_sent_again() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _cut_response()

    boundary = RetryBoundary()
    with pytest.raises(ModelTransportHold, match="OAUTH_TRANSPORT_FAILURE"):
        await _run(handler, _work(boundary, auto=False))
    assert calls == 1 and boundary.reservations == 1


@pytest.mark.asyncio
async def test_switched_on_a_cut_off_call_is_sent_again_and_recorded_as_a_retry() -> None:
    payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        return _cut_response() if len(payloads) == 1 else httpx.Response(200, content=_completed())

    boundary = RetryBoundary()
    waits = Waits()
    result = await _run(handler, _work(boundary, auto=True), waits)
    assert result.output.label == "ok"
    assert waits.seconds == [2.0]
    assert len(payloads) == 2 and payloads[0] == payloads[1]
    assert boundary.dispatch_ids == ["call:0", "call:1"]
    # the cut-off call is recorded as unfinished, the second as the retry of the first
    assert boundary.retries == [None, "call:0"]
    assert boundary.remote_stops[0] == "UNKNOWN"


@pytest.mark.asyncio
async def test_a_call_is_retried_at_most_twice_and_then_shows_the_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _cut_response()

    boundary = RetryBoundary()
    waits = Waits()
    with pytest.raises(ModelTransportHold, match="OAUTH_TRANSPORT_FAILURE"):
        await _run(handler, _work(boundary, auto=True), waits)
    # the first send plus two retries, waiting two and then four seconds
    assert calls == 3 and boundary.reservations == 3
    assert waits.seconds == [2.0, 4.0]
    assert boundary.retries == [None, "call:0", "call:1"]


@pytest.mark.asyncio
async def test_the_429_retry_and_the_cut_off_retries_share_the_limit_of_two() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                content=b'{"error":{"code":"rate_limit_exceeded"}}',
                headers={"retry-after": "0", "content-type": "application/json"},
            )
        return _cut_response()

    boundary = RetryBoundary()
    with pytest.raises(ModelTransportHold, match="OAUTH_TRANSPORT_FAILURE"):
        await _run(handler, _work(boundary, auto=True, retry_429=True))
    # one 429 retry and one cut-off retry used the two; a third cut-off is not sent again
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(401, content=b"{}"), "OAUTH_AUTH_REQUIRED_401"),
        (
            httpx.Response(400, content=b'{"error":{"code":"invalid_request"}}'),
            "OAUTH_REQUEST_REJECTED_400",
        ),
        (
            httpx.Response(429, content=b'{"error":{"code":"usage_limit_reached"}}'),
            "OAUTH_REQUEST_REJECTED_429",
        ),
        (
            httpx.Response(
                200,
                content=b'data: {"type":"response.output_text.delta","delta":"x"}\n\n'
                b"data: not json\n\n",
            ),
            "OAUTH_INVALID_SSE",
        ),
    ],
)
async def test_authentication_format_and_usage_limit_failures_are_never_retried(
    response: httpx.Response, reason: str
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    waits = Waits()
    with pytest.raises(ModelTransportHold, match=reason):
        await _run(handler, _work(RetryBoundary(), auto=True), waits)
    assert calls == 1 and waits.seconds == []


@pytest.mark.asyncio
async def test_a_call_stopped_by_its_total_limit_is_retried_like_a_cut_off_one() -> None:
    calls = 0

    class Hangs(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"type":"response.output_text.delta","delta":"x"}\n\n'
            await asyncio.sleep(5)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=Hangs()
            )
        return httpx.Response(200, content=_completed())

    boundary = RetryBoundary()
    result = await _run(
        handler,
        _work(boundary, auto=True),
        timeout_policy=TransportTimeouts(dispatch_total_seconds=Decimal("0.05")),
    )
    assert result.output.label == "ok" and calls == 2
    assert boundary.retries == [None, "call:0"]
