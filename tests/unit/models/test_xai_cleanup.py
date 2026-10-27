"""Closing an xAI response survives a second cancel and is bounded.

The same properties as the Codex transport (see test_oauth_receive_observation): a close is not cut
by the attempt's own deadline expiring during cleanup, a close that never ends is given up after its
own limit with the observation saying so, and a cancel that arrives during cleanup is raised.
Deadlines run on the loop clock, which the test moves once the close has begun.
"""

import asyncio

import httpx
import pytest
from tests.unit.models.test_oauth_receive_observation import (
    ClosingStream,
    Deadlines,
    FinishedStream,
)

from thoth.adapters.models.close_within import CLEANUP_SECONDS
from thoth.adapters.models.xai_oauth import XaiSession
from thoth.adapters.models.xai_responses import XaiResponsesExecutor
from thoth.domain.model_dispatch import PreparedModelDispatch
from thoth.ports.model import ModelTransportCancelled

pytestmark = pytest.mark.usefixtures("xai_http_guard")


class Reader:
    def read(self) -> XaiSession:
        return XaiSession("xai-secret-token", "grok-4.6")


def executor_for(stream: ClosingStream) -> tuple[XaiResponsesExecutor, PreparedModelDispatch]:
    executor = XaiResponsesExecutor(
        Reader(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)),
    )
    prepared = executor.prepare("p", {"type": "object"}, output_tokens=100, timeout_seconds=60.0)
    return executor, prepared


@pytest.mark.asyncio
async def test_a_deadline_that_expires_while_the_response_closes_does_not_cut_the_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ClosingStream(close_seconds=0.5)
    executor, prepared = executor_for(stream)
    clock = Deadlines(monkeypatch)
    task = asyncio.create_task(executor.dispatch(prepared))
    await asyncio.wait_for(stream.received.wait(), 5)
    task.cancel()
    await asyncio.wait_for(stream.closing.wait(), 5)
    # The attempt's own 60 s deadline comes due while the close is still running.
    clock.expire(70)
    with pytest.raises(ModelTransportCancelled) as raised:
        await asyncio.wait_for(task, 5)
    assert stream.closed
    assert raised.value.observation.transport_closed is True


@pytest.mark.asyncio
async def test_a_close_that_never_ends_is_given_up_and_the_cancel_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ClosingStream(close_seconds=None)
    executor, prepared = executor_for(stream)
    clock = Deadlines(monkeypatch)
    task = asyncio.create_task(executor.dispatch(prepared))
    await asyncio.wait_for(stream.received.wait(), 5)
    task.cancel()
    await asyncio.wait_for(stream.closing.wait(), 5)
    clock.expire(CLEANUP_SECONDS + 0.5)
    with pytest.raises(ModelTransportCancelled) as raised:
        await asyncio.wait_for(task, 5)
    assert not stream.closed
    observed = raised.value.observation
    assert observed.transport_closed is False and observed.received_bytes > 0


@pytest.mark.asyncio
async def test_a_cancel_that_arrives_while_a_finished_response_closes_is_raised() -> None:
    stream = FinishedStream(close_seconds=0.5)
    executor, prepared = executor_for(stream)
    task = asyncio.create_task(executor.dispatch(prepared))
    await asyncio.wait_for(stream.closing.wait(), 5)
    task.cancel()
    # The answer was complete, so without the cancel this returns a reply. The cancel must win.
    with pytest.raises(ModelTransportCancelled) as raised:
        await asyncio.wait_for(task, 5)
    assert stream.closed and raised.value.observation.local_cancel_requested
