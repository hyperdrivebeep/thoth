"""A model stream that keeps arriving but slows to a crawl, or runs too long, is stopped locally.

The pace rules (T2) and the per-call total limit (T3) are measured against a fake clock and a fake
transport, so no test waits in real time.
"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
from tests.unit.models.test_transport_phase_contract import Session

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.stream_pace import (
    CODEX_PACE_DEFAULTS,
    StreamPace,
    with_pace_defaults,
)
from thoth.domain.model_dispatch import ModelTransportReply, TransportTimeouts
from thoth.ports.model import ModelTransportHold


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _frame(kind: str, **fields: object) -> bytes:
    return b"data: " + json.dumps({"type": kind, **fields}).encode() + b"\n\n"


def _completed(text: str) -> bytes:
    done = {
        "id": "resp-1",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    return _frame("response.completed", response=done)


def _stream(clock: Clock, steps: list[tuple[float, int]], finish: bool) -> httpx.Response:
    """Each step advances the fake clock and then sends that many output deltas in one chunk."""

    async def body() -> AsyncIterator[bytes]:
        for seconds, deltas in steps:
            clock.now += seconds
            yield b"".join(_frame("response.output_text.delta", delta="x") for _ in range(deltas))
        if finish:
            yield _completed("x")

    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_AsyncBody(body())
    )


class _AsyncBody(httpx.AsyncByteStream):
    def __init__(self, inner: AsyncIterator[bytes]) -> None:
        self.inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.inner:
            yield chunk


def _executor(
    clock: Clock, steps: list[tuple[float, int]], finish: bool = True, **policy: object
) -> CodexHttpExecutor:
    return CodexHttpExecutor(
        Session(),
        transport=httpx.MockTransport(lambda request: _stream(clock, steps, finish)),
        timeout_policy=TransportTimeouts(**policy) if policy else None,  # type: ignore[arg-type]
        clock=clock,
    )


async def _dispatch(executor: CodexHttpExecutor):  # type: ignore[no-untyped-def]
    prepared = executor.prepare("p", {"type": "object"}, output_tokens=100, timeout_seconds=None)
    return await executor.dispatch(prepared)


def test_the_defaults_rest_on_the_measured_calls() -> None:
    # 27 real calls: the slowest normal one averaged 16.9 output events a second, the stalled 1.4.
    assert CODEX_PACE_DEFAULTS.stall_min_events_per_second == Decimal(5)
    assert CODEX_PACE_DEFAULTS.stall_warmup_seconds == Decimal(120)
    assert CODEX_PACE_DEFAULTS.dispatch_total_seconds == Decimal(720)
    explicit = TransportTimeouts(stall_min_events_per_second=Decimal(9))
    assert with_pace_defaults(explicit).stall_min_events_per_second == Decimal(9)
    assert with_pace_defaults(explicit).stall_window_seconds == Decimal(60)


def test_a_normal_pace_is_never_stopped_however_long_it_runs() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    for _ in range(600):  # ten minutes at the slowest normal call's pace, 17 events a second
        clock.now += 1
        for _ in range(17):
            pace.observe()
        assert not pace.stalled()


def test_no_judgement_before_the_first_output_event_or_inside_the_warm_up() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    clock.now += 600  # a long silent reasoning phase before any output
    assert not pace.stalled()
    pace.observe()
    clock.now += 100  # still inside the 120 s warm-up after the first event
    assert not pace.stalled()


def test_a_pace_below_the_limit_for_the_sustain_time_is_a_stall() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    pace.observe()
    stalled_at = None
    for second in range(1, 600):
        clock.now += 1
        if second % 2 == 0:  # 0.5 events a second
            pace.observe()
        if pace.stalled():
            stalled_at = second
            break
    # warm-up 120 s, then the window must stay under the limit for 120 s
    assert stalled_at is not None and 240 <= stalled_at <= 300


def test_a_recovery_inside_the_sustain_time_resets_it() -> None:
    clock = Clock()
    pace = StreamPace(CODEX_PACE_DEFAULTS, clock)
    pace.observe()
    for _ in range(130):  # slow right after the warm-up ...
        clock.now += 1
        pace.stalled()
    for _ in range(100):  # ... then fast again before the sustain time is up
        clock.now += 1
        for _ in range(20):
            pace.observe()
        assert not pace.stalled()
    for _ in range(60):  # one slow minute more is not enough
        clock.now += 1
        assert not pace.stalled()


async def test_a_slow_stream_that_keeps_arriving_is_stopped_with_the_stall_reason() -> None:
    clock = Clock()
    steps = [(0.0, 50)] + [(10.0, 1)] * 60  # a burst, then one event every ten seconds
    executor = _executor(clock, steps, finish=True)
    with pytest.raises(
        ModelTransportHold, match="OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN"
    ) as held:
        await _dispatch(executor)
    observation = held.value.observation
    assert observation.http_status == 200
    assert observation.output_event_count is not None and observation.output_event_count < 110
    assert observation.window_events_per_second is not None
    assert observation.window_events_per_second < 5
    assert observation.stall_limit_events_per_second == 5.0


async def test_a_steady_stream_completes_and_reports_its_pace() -> None:
    clock = Clock()
    steps = [(1.0, 20)] * 400  # eight minutes at 20 events a second
    reply = await _dispatch(_executor(clock, steps))
    assert reply.text == "x" * 8000
    assert reply.observation is not None
    assert reply.observation.output_event_count == 8000
    assert reply.observation.window_events_per_second is not None
    assert reply.observation.window_events_per_second >= 5


async def test_a_stream_before_its_first_output_event_is_not_judged_by_pace() -> None:
    clock = Clock()
    steps = [(300.0, 0), (1.0, 30)]  # five silent minutes, then normal output
    reply = await _dispatch(_executor(clock, steps))
    assert reply.text == "x" * 30


async def test_a_call_over_its_total_limit_is_stopped_with_the_deadline_reason() -> None:
    import asyncio

    async def never_ends() -> AsyncIterator[bytes]:
        yield _frame("response.output_text.delta", delta="x")
        await asyncio.sleep(5)

    response = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_AsyncBody(never_ends())
    )
    executor = CodexHttpExecutor(
        Session(),
        transport=httpx.MockTransport(lambda request: response),
        timeout_policy=TransportTimeouts(dispatch_total_seconds=Decimal("0.05")),
    )
    with pytest.raises(
        ModelTransportHold, match="OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN"
    ) as held:
        await _dispatch(executor)
    assert held.value.observation.timeout_kind == "DISPATCH_TOTAL"
    assert held.value.observation.dispatch_total_limit_seconds == Decimal("0.05")


async def test_a_call_inside_its_total_limit_completes() -> None:
    clock = Clock()
    reply = await _dispatch(_executor(clock, [(1.0, 30)], dispatch_total_seconds=Decimal(30)))
    assert reply.text == "x" * 30


def _text_stream(texts: list[str], finish: bool = True) -> httpx.Response:
    """One output delta per text, all in one chunk, then (optionally) a normal completion."""

    async def body() -> AsyncIterator[bytes]:
        yield b"".join(_frame("response.output_text.delta", delta=text) for text in texts)
        if finish:
            yield _completed("x")

    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_AsyncBody(body())
    )


async def _dispatch_text(
    texts: list[str], *, output_tokens: int = 100, **policy: object
) -> ModelTransportReply:
    executor = CodexHttpExecutor(
        Session(),
        transport=httpx.MockTransport(lambda request: _text_stream(texts)),
        timeout_policy=TransportTimeouts(**policy) if policy else None,  # type: ignore[arg-type]
    )
    prepared = executor.prepare(
        "p", {"type": "object"}, output_tokens=output_tokens, timeout_seconds=None
    )
    return await executor.dispatch(prepared)


def test_the_runaway_defaults_rest_on_the_measured_calls() -> None:
    assert CODEX_PACE_DEFAULTS.runaway_max_output_events == 25_000
    assert CODEX_PACE_DEFAULTS.runaway_blank_run == 2_000
    # 8 visible bytes per requested output token, never below the floor
    assert StreamPace(CODEX_PACE_DEFAULTS, output_tokens=6_000).max_visible_bytes == 48_000
    assert StreamPace(CODEX_PACE_DEFAULTS, output_tokens=256).max_visible_bytes == 16_384
    explicit = TransportTimeouts(runaway_blank_run=7)
    assert StreamPace(explicit).max_blank_run == 7
    assert StreamPace(explicit).max_output_events == 25_000


@pytest.mark.parametrize(
    ("texts", "policy", "limit"),
    [
        (["x"] * 60, {"runaway_max_output_events": 50}, "OUTPUT_EVENTS"),
        (["x" * 50] * 20, {"runaway_min_visible_bytes": 1}, "VISIBLE_BYTES"),
        ([" ", "\n", "  "] * 4, {"runaway_blank_run": 10}, "BLANK_RUN"),
    ],
)
async def test_an_output_that_never_ends_is_stopped_by_the_limit_it_crossed(
    texts: list[str], policy: dict[str, object], limit: str
) -> None:
    with pytest.raises(
        ModelTransportHold, match="OAUTH_RUNAWAY_OUTPUT_REMOTE_STOP_UNKNOWN"
    ) as held:
        await _dispatch_text(texts, **cast(dict[str, Any], policy))
    observation = held.value.observation
    assert observation.runaway_limit == limit
    assert observation.http_status == 200
    assert observation.runaway_max_output_events is not None
    # 8 bytes for each of the 100 requested tokens, unless the floor was lowered to let it show
    assert observation.runaway_max_visible_bytes == (
        800 if "runaway_min_visible_bytes" in policy else 16_384
    )
    assert observation.runaway_blank_run is not None


async def test_a_normal_json_with_blank_runs_between_its_tokens_is_not_stopped() -> None:
    pretty = json.dumps(
        {"label": "ok", "items": [{"id": n, "note": "근거"} for n in range(40)]}, indent=4
    )
    # tokens, with the indentation and line breaks arriving as blank-only events of their own
    pieces: list[str] = []
    for line in pretty.split("\n"):
        stripped = line.lstrip()
        pieces += [" " * (len(line) - len(stripped)), stripped, "\n"]
    reply = await _dispatch_text(pieces, output_tokens=6_000)
    assert reply.text == "".join(pieces)
    # 1,999 blank events in a row is still one short of the default limit
    reply = await _dispatch_text([" "] * 1_999 + ["x"], output_tokens=6_000)
    assert reply.text.endswith("x")
    # and a run exactly at the limit is a runaway
    with pytest.raises(ModelTransportHold, match="OAUTH_RUNAWAY_OUTPUT_REMOTE_STOP_UNKNOWN"):
        await _dispatch_text([" "] * 2_000, output_tokens=6_000)


async def test_a_long_but_ordinary_reply_inside_every_limit_completes() -> None:
    # about twice the largest measured normal call's events, well under 8 bytes per token
    texts = ["ab"] * 20_000
    reply = await _dispatch_text(texts, output_tokens=6_000)
    assert len(reply.text) == 40_000
    assert reply.observation is not None and reply.observation.runaway_limit is None
