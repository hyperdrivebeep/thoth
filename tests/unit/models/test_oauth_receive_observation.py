"""Deadline/cancel must preserve real receive evidence without swallowing cancellation."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest

from thoth.adapters.models.codex_http import CLEANUP_SECONDS, CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.application.services.research_models import ResearchModel
from thoth.domain.base import DomainModel
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.model_dispatch import (
    ModelControlCapability,
    ModelReceiveObservation,
    OAuthSession,
)
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelExecutionHold, ModelTransportCancelled, ModelTransportHold


class Session:
    def read(self) -> OAuthSession:
        return OAuthSession("fixture-token", "fixture-account", "test", "xhigh")


class Ping(DomainModel):
    label: str


class StalledStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.received = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for event in (
            {"type": "response.created", "response": {"id": "observed-response"}},
            {"type": "response.output_text.delta", "delta": '{"label":'},
        ):
            yield b"data: " + json.dumps(event).encode() + b"\n\n"
        self.received.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class Boundary:
    def __init__(self, call_timeouts: Sequence[float] = (60.0, 120.0)) -> None:
        self.observations: list[ModelReceiveObservation] = []
        self.remote_stops: list[str] = []
        self.response_ids: list[str | None] = []
        self.reservations = 0
        # The first call is the outermost deadline; every later call gets the last value, so the
        # attempt's own deadline is longer than the one around it.
        self._call_timeouts = list(call_timeouts)

    def check(self) -> None:
        pass

    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None:
        pass

    def transport(self, payload_bytes: int) -> None:
        pass

    def call_timeout(self) -> float:
        return (
            self._call_timeouts.pop(0) if len(self._call_timeouts) > 1 else self._call_timeouts[0]
        )

    def owns_attempt(self) -> bool:
        return True

    def new_model_call(self) -> str:
        return "call"

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        self.reservations += 1

    def record_usage(
        self,
        dispatch_id: str,
        received_bytes: int,
        input_tokens: int | None,
        output_tokens: int | None,
        remote_stop: str,
        response_id: str | None,
        observation: ModelReceiveObservation | None = None,
        retry_of_dispatch_id: str | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        if observation is not None:
            assert received_bytes == observation.received_bytes
        if input_tokens is None and output_tokens is None:
            assert observation is not None
        assert observation is not None
        self.observations.append(observation)
        self.remote_stops.append(remote_stop)
        self.response_ids.append(response_id)


class Deadlines:
    """Deadlines run on the loop's clock, so the test moves that clock instead of waiting for it.

    A deadline that counts wall time from the call starts before the connection exists. On a slow
    runner it can expire before any stream was opened, and then there is nothing to close or record.
    Here the deadlines are far away until the test has seen the stream's frames and expires them.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.loop = asyncio.get_running_loop()
        self.offset = 0.0
        real = self.loop.time
        monkeypatch.setattr(self.loop, "time", lambda: real() + self.offset)

    def expire(self, seconds: float) -> None:
        self.offset += seconds
        # The loop may be asleep until a timer computed on the old clock; give it work now.
        self.loop.call_soon(lambda: None)


def model_request() -> ModelRequest[Ping]:
    return ModelRequest(
        role=ModelRole.USER_EXPLAINER,
        project_id="test",
        cutoff_at=datetime.now(UTC),
        context_pack=ContextPack(
            case_id="c",
            project_id="test",
            object_id="o",
            problem="Ping",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest="0" * 64,
        ),
        output_model=Ping,
        prompt_version="fixture",
        model_policy_ref="fixture",
        max_output_tokens=100,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct-deadline", "outer-deadline", "caller-cancel"])
async def test_received_metadata_survives_deadline_and_cancel(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, boundary = StalledStream(), Boundary()
    clock = Deadlines(monkeypatch)
    executor = CodexHttpExecutor(
        Session(), transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    delegate = CodexOAuthModel(executor, max_repair_attempts=0)
    model = delegate if mode == "direct-deadline" else ResearchModel(delegate)
    ref = RevisionRef(
        project_id="test",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="revision:r",
        revision_digest="0" * 64,
        schema_version="2.0.0",
    )
    token = research_work.set(ResearchWork(ref, "Ping", boundary))
    try:
        task = asyncio.create_task(model.structured(model_request()))
        await asyncio.wait_for(stream.received.wait(), 5)
        if mode == "caller-cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            clock.expire(90)
            with pytest.raises(ModelExecutionHold) as raised:
                await task
            if mode == "outer-deadline":
                assert str(raised.value) == "MODEL_CALL_TIME_BUDGET_EXHAUSTED"
            else:
                assert isinstance(raised.value, ModelTransportHold)
        assert stream.closed and boundary.reservations == 1
        assert len(boundary.observations) == 1
        observed = boundary.observations[0]
        assert observed.http_status == 200 and observed.received_bytes > 0
        assert observed.visible_output_bytes == len('{"label":')
        assert observed.first_response_ms is not None and observed.first_byte_ms is not None
        assert observed.elapsed_ms is not None
        assert observed.last_event_type == "response.output_text.delta"
        assert boundary.response_ids == ["observed-response"]
        assert boundary.remote_stops == ["UNKNOWN"]
    finally:
        research_work.reset(token)


def test_old_observation_still_decodes() -> None:
    observation = ModelReceiveObservation(
        received_bytes=0,
        visible_output_bytes=0,
        frame_counts={},
        max_stream_bytes=100,
        max_visible_output_bytes=50,
        timeout_ms=1000,
    )
    assert observation.http_status is None and observation.response_id is None
    assert observation.first_byte_ms is None


@pytest.mark.asyncio
async def test_outer_deadline_reports_exhausted_attempt_without_resetting_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExpiringBoundary(Boundary):
        expired = False

        def call_timeout(self) -> float:
            if self.expired:
                raise ModelExecutionHold("TOTAL_RESEARCH_BUDGET_EXHAUSTED")
            return super().call_timeout()

    boundary = ExpiringBoundary()
    clock = Deadlines(monkeypatch)

    class ClosingStream(StalledStream):
        async def aclose(self) -> None:
            await super().aclose()
            boundary.expired = True

    stream = ClosingStream()
    executor = CodexHttpExecutor(
        Session(), transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    ref = RevisionRef(
        project_id="test",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="revision:r",
        revision_digest="0" * 64,
        schema_version="2.0.0",
    )
    token = research_work.set(ResearchWork(ref, "Ping", boundary))
    try:
        task = asyncio.create_task(
            ResearchModel(CodexOAuthModel(executor)).structured(model_request())
        )
        await asyncio.wait_for(stream.received.wait(), 5)
        clock.expire(90)
        with pytest.raises(ModelExecutionHold, match="TOTAL_RESEARCH_BUDGET_EXHAUSTED"):
            await task
        assert boundary.reservations == 1 and len(boundary.observations) == 1
        assert stream.closed
    finally:
        research_work.reset(token)


class ClosingStream(StalledStream):
    """A stream whose close takes real time, or never ends; `closing` says the close began."""

    def __init__(self, close_seconds: float | None) -> None:
        super().__init__()
        self.closing = asyncio.Event()
        self.close_seconds = close_seconds

    async def aclose(self) -> None:
        self.closing.set()
        if self.close_seconds is None:
            await asyncio.Event().wait()
        else:
            await asyncio.sleep(self.close_seconds)
            self.closed = True


async def cancel_during_close(
    stream: ClosingStream, monkeypatch: pytest.MonkeyPatch, *, expire_seconds: float
) -> tuple[Boundary, asyncio.Task[object]]:
    """Cancel the caller mid-stream, wait until the stream's close has begun, then move the clock.

    The attempt's own deadline is 1 s on the loop clock, so moving the clock by `expire_seconds`
    makes it, and the close's own limit if that is shorter, come due while the close still runs.
    The caller's 2 s wait for the attempt to wind down stays out of reach.
    """
    boundary, clock = Boundary((60.0, 1.0)), Deadlines(monkeypatch)
    executor = CodexHttpExecutor(
        Session(), transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    model = ResearchModel(CodexOAuthModel(executor, max_repair_attempts=0))
    ref = RevisionRef(
        project_id="test",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="revision:r",
        revision_digest="0" * 64,
        schema_version="2.0.0",
    )
    research_work.set(ResearchWork(ref, "Ping", boundary))
    task = asyncio.create_task(model.structured(model_request()))
    await asyncio.wait_for(stream.received.wait(), 5)
    task.cancel()
    await asyncio.wait_for(stream.closing.wait(), 5)
    clock.expire(expire_seconds)
    return boundary, cast(asyncio.Task[object], task)


@pytest.mark.asyncio
async def test_a_deadline_that_expires_while_the_stream_closes_does_not_cut_the_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ClosingStream(close_seconds=0.5)
    boundary, task = await cancel_during_close(stream, monkeypatch, expire_seconds=1.5)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert stream.closed
    assert len(boundary.observations) == 1
    assert boundary.observations[0].transport_closed is True


@pytest.mark.asyncio
async def test_a_close_that_never_ends_is_given_up_after_its_own_limit_and_the_cancel_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ClosingStream(close_seconds=None)
    boundary, task = await cancel_during_close(
        stream, monkeypatch, expire_seconds=CLEANUP_SECONDS + 0.5
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert not stream.closed
    assert len(boundary.observations) == 1
    observed = boundary.observations[0]
    assert observed.transport_closed is False and observed.received_bytes > 0


class FinishedStream(ClosingStream):
    """A stream that ends normally with a complete answer, then takes time to close."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        output = {
            "type": "message",
            "content": [{"type": "output_text", "text": '{"label":"ok"}'}],
        }
        for event in (
            {"type": "response.created", "response": {"id": "done-response"}},
            {"type": "response.output_text.delta", "delta": '{"label":"ok"}'},
            {"type": "response.completed", "response": {"id": "done-response", "output": [output]}},
        ):
            yield b"data: " + json.dumps(event).encode() + b"\n\n"
        self.received.set()


@pytest.mark.asyncio
async def test_a_cancel_that_arrives_while_a_finished_answer_is_closing_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = FinishedStream(close_seconds=0.5)
    boundary, clock = Boundary(), Deadlines(monkeypatch)
    executor = CodexHttpExecutor(
        Session(), transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    model = ResearchModel(CodexOAuthModel(executor, max_repair_attempts=0))
    ref = RevisionRef(
        project_id="test",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="revision:r",
        revision_digest="0" * 64,
        schema_version="2.0.0",
    )
    research_work.set(ResearchWork(ref, "Ping", boundary))
    task = asyncio.create_task(model.structured(model_request()))
    await asyncio.wait_for(stream.closing.wait(), 5)
    # The answer is complete and nothing is cancelled yet. The only cancel comes during the close.
    task.cancel()
    clock.expire(0)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert stream.closed


@pytest.mark.asyncio
async def test_the_executor_raises_a_cancel_that_arrives_while_a_finished_response_closes() -> None:
    stream = FinishedStream(close_seconds=0.5)
    executor = CodexHttpExecutor(
        Session(), transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    prepared = executor.prepare("p", {"type": "object"}, output_tokens=100, timeout_seconds=60.0)
    task = asyncio.create_task(executor.dispatch(prepared))
    await asyncio.wait_for(stream.closing.wait(), 5)
    task.cancel()
    # The answer was complete, so without the cancel this returns a reply. The cancel must win.
    with pytest.raises(ModelTransportCancelled) as raised:
        await asyncio.wait_for(task, 5)
    assert stream.closed and raised.value.observation.local_cancel_requested
