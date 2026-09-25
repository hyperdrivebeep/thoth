"""Deadline/cancel must preserve real receive evidence without swallowing cancellation."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from thoth.adapters.models.codex_http import CodexHttpExecutor
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
from thoth.ports.model import ModelExecutionHold, ModelTransportHold


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
    def __init__(self) -> None:
        self.observations: list[ModelReceiveObservation] = []
        self.remote_stops: list[str] = []
        self.response_ids: list[str | None] = []
        self.reservations = 0

    def check(self) -> None:
        pass

    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None:
        pass

    def transport(self, payload_bytes: int) -> None:
        pass

    def call_timeout(self) -> float:
        return 0.15

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
async def test_received_metadata_survives_deadline_and_cancel(mode: str) -> None:
    stream, boundary = StalledStream(), Boundary()
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
        if mode == "caller-cancel":
            task = asyncio.create_task(model.structured(model_request()))
            await asyncio.wait_for(stream.received.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ModelExecutionHold) as raised:
                await model.structured(model_request())
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
async def test_outer_deadline_reports_exhausted_attempt_without_resetting_budget() -> None:
    class ExpiringBoundary(Boundary):
        expired = False

        def call_timeout(self) -> float:
            if self.expired:
                raise ModelExecutionHold("TOTAL_RESEARCH_BUDGET_EXHAUSTED")
            return 0.03

    boundary = ExpiringBoundary()

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
        with pytest.raises(ModelExecutionHold, match="TOTAL_RESEARCH_BUDGET_EXHAUSTED"):
            await ResearchModel(CodexOAuthModel(executor)).structured(model_request())
        assert boundary.reservations == 1 and len(boundary.observations) == 1
        assert stream.closed
    finally:
        research_work.reset(token)
