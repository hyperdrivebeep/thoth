"""Stall observation: when the last output event came, the longest wait between output events,
and the error code a remote reported. Fake clock and fake transport; no waiting, no network."""

import json
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from tests.unit.models.test_stream_pace import Clock
from tests.unit.models.test_transport_phase_contract import Session

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.response_events import (
    OBSERVED_ERROR_CODES,
    ResponseEvent,
    observed_error_code,
)
from thoth.adapters.models.stream_pace import CODEX_PACE_DEFAULTS, StreamPace
from thoth.domain.canonical import canonical_payload
from thoth.domain.model_dispatch import ModelReceiveObservation, ModelTransportReply
from thoth.ports.model import ModelTransportHold


def _frame(kind: str, **fields: object) -> bytes:
    return b"data: " + json.dumps({"type": kind, **fields}).encode() + b"\n\n"


class _AsyncBody(httpx.AsyncByteStream):
    def __init__(self, inner: AsyncIterator[bytes]) -> None:
        self.inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.inner:
            yield chunk


def _completed() -> bytes:
    done = {
        "id": "resp-1",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "x"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    return _frame("response.completed", response=done)


def _executor(
    clock: Clock, steps: list[tuple[float, int]], finish: bool = True
) -> CodexHttpExecutor:
    """Each step advances the fake clock and then sends that many output deltas in one chunk."""

    async def body() -> AsyncIterator[bytes]:
        for seconds, deltas in steps:
            clock.now += seconds
            yield b"".join(_frame("response.output_text.delta", delta="x") for _ in range(deltas))
        if finish:
            yield _completed()

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_AsyncBody(body())
        )

    return CodexHttpExecutor(Session(), transport=httpx.MockTransport(respond), clock=clock)


async def _dispatch(executor: CodexHttpExecutor) -> ModelTransportReply:
    prepared = executor.prepare("p", {"type": "object"}, output_tokens=100, timeout_seconds=None)
    return await executor.dispatch(prepared)


def test_no_output_event_means_no_times() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    clock.now += 30
    assert pace.last_output_event_ms is None and pace.max_output_gap_ms is None


def test_one_output_event_has_a_time_but_no_gap() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    clock.now += 2.5
    pace.observe()
    assert pace.last_output_event_ms == 2500
    assert pace.max_output_gap_ms is None


def test_the_longest_wait_between_events_and_the_last_event_time() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    for wait in (2, 5, 1, 3):
        clock.now += wait
        pace.observe()
    clock.now += 200  # silence after the last event is neither a gap nor part of the last time
    assert pace.last_output_event_ms == 11_000
    assert pace.max_output_gap_ms == 5000


def test_observing_does_not_change_the_stall_decision() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    pace.observe()
    stalled_at = None
    for second in range(1, 600):
        clock.now += 1
        if second % 2 == 0:
            pace.observe()
        if pace.stalled():
            stalled_at = second
            break
    assert stalled_at is not None and 240 <= stalled_at <= 300


async def test_a_completed_call_reports_both_times() -> None:
    clock = Clock()
    reply = await _dispatch(_executor(clock, [(2.0, 1), (7.0, 1), (1.0, 1)]))
    assert reply.observation is not None
    assert reply.observation.last_output_event_ms == 10_000
    assert reply.observation.max_output_gap_ms == 7000
    assert reply.observation.remote_error_code is None


async def test_a_stalled_call_shows_when_it_went_quiet() -> None:
    clock = Clock()
    steps = [(0.0, 50)] + [(10.0, 1)] * 60
    with pytest.raises(ModelTransportHold, match="STALLED_STREAM") as held:
        await _dispatch(_executor(clock, steps, finish=True))
    observation = held.value.observation
    assert observation.last_output_event_ms is not None
    assert observation.max_output_gap_ms == 10_000
    assert observation.last_output_event_ms <= round(clock.now - 1000.0) * 1000


def test_older_records_without_the_new_fields_still_read_and_roundtrip() -> None:
    observation = ModelReceiveObservation(
        received_bytes=1,
        visible_output_bytes=0,
        frame_counts={},
        max_stream_bytes=None,
        max_visible_output_bytes=None,
        timeout_ms=None,
        last_output_event_ms=1500,
        max_output_gap_ms=900,
        remote_error_code="server_error",
        window_events_per_second=Decimal("1.5"),
    )
    assert canonical_payload(observation)
    assert ModelReceiveObservation.model_validate_json(observation.model_dump_json()) == observation
    legacy = observation.model_dump(mode="json")
    for field in ("last_output_event_ms", "max_output_gap_ms", "remote_error_code"):
        legacy.pop(field)
    old = ModelReceiveObservation.model_validate(legacy)
    assert (old.last_output_event_ms, old.max_output_gap_ms, old.remote_error_code) == (
        None,
        None,
        None,
    )


def test_a_code_outside_the_list_is_not_a_valid_stored_value() -> None:
    with pytest.raises(ValueError):
        ModelReceiveObservation.model_validate(
            {
                "received_bytes": 0,
                "visible_output_bytes": 0,
                "frame_counts": {},
                "max_stream_bytes": None,
                "max_visible_output_bytes": None,
                "timeout_ms": None,
                "remote_error_code": "some_free_text_from_a_remote",
            }
        )


def _event(kind: str, **fields: object) -> ResponseEvent:
    return ResponseEvent.model_validate({"type": kind, **fields})


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_event("error", code="server_error"), "server_error"),
        (_event("error", code="rate_limit_exceeded"), "rate_limit_exceeded"),
        (_event("error", error={"code": "usage_limit_reached"}), "usage_limit_reached"),
        (_event("error", code="something_new"), "OTHER"),
        (_event("error", error={"code": "something_new"}), "OTHER"),
        (_event("error", message="no code here"), None),
        (_event("error", code=5), None),
        (_event("error", error="oops"), None),
        (
            _event("response.failed", response={"error": {"code": "invalid_prompt"}}),
            "invalid_prompt",
        ),
        (_event("response.failed", response={"error": {"code": "weird"}}), "OTHER"),
        (_event("response.failed", response={"id": "r"}), None),
        (_event("response.failed", response={"error": "text"}), None),
        (_event("response.failed"), None),
        (_event("response.incomplete", response={"error": {"code": "server_error"}}), None),
        (_event("response.output_text.delta", delta="x", code="server_error"), None),
    ],
)
def test_only_listed_codes_are_kept(event: ResponseEvent, expected: str | None) -> None:
    assert observed_error_code(event) == expected


def test_every_listed_code_has_a_source_and_other_is_not_listed() -> None:
    assert "OTHER" not in OBSERVED_ERROR_CODES
    assert {
        "server_error",
        "rate_limit_exceeded",
        "invalid_prompt",
        "data_residency_mismatch",
        "bio_policy",
        "vector_store_timeout",
        "insufficient_quota",
        "usage_limit_reached",
    } == OBSERVED_ERROR_CODES


def _error_executor(*frames: bytes) -> CodexHttpExecutor:
    async def body():  # type: ignore[no-untyped-def]
        for frame in frames:
            yield frame

    response = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_AsyncBody(body())
    )
    return CodexHttpExecutor(Session(), transport=httpx.MockTransport(lambda r: response))


SECRET = "SECRET-ERROR-TEXT-do-not-store"


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (_frame("error", code="server_error", message=SECRET), "server_error"),
        (_frame("error", code="brand_new_code", message=SECRET), "OTHER"),
        (_frame("error", message=SECRET), None),
        (
            _frame(
                "response.failed",
                response={"error": {"code": "rate_limit_exceeded", "message": SECRET}},
            ),
            "rate_limit_exceeded",
        ),
        (_frame("response.failed", response={"id": "r", "error": {"message": SECRET}}), None),
    ],
)
async def test_a_remote_error_is_recorded_by_code_only(frame: bytes, expected: str | None) -> None:
    executor = _error_executor(_frame("response.created"), _frame("response.in_progress"), frame)
    with pytest.raises(ModelTransportHold, match="OAUTH_INCOMPLETE_RESPONSE") as held:
        await _dispatch(executor)
    observation = held.value.observation
    assert observation.remote_error_code == expected
    assert observation.last_event_type in {"error", "response.failed"}
    assert SECRET not in observation.model_dump_json()
    assert SECRET not in json.dumps(canonical_payload(observation), default=str)


async def test_an_incomplete_response_records_no_code() -> None:
    executor = _error_executor(
        _frame("response.created"), _frame("response.incomplete", response={"id": "r"})
    )
    with pytest.raises(ModelTransportHold, match="OAUTH_INCOMPLETE_RESPONSE") as held:
        await _dispatch(executor)
    assert held.value.observation.remote_error_code is None
