"""Asking the AI to re-examine a hypothesis goes through the normal research loop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage import SqliteConversationSessionStore
from thoth.application.commands.judgment_review import JudgmentReviewHandlers
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.conversation import ConversationDispatchOutcome
from thoth.ports.conversation import ConversationDispatcherPort

Json = dict[str, JsonValue]


class Failing(ConversationDispatcherPort):
    async def dispatch(
        self, *, method: str, arguments: Json, idempotency_key: str
    ) -> ConversationDispatchOutcome:
        return ConversationDispatchOutcome(success=False, error_message="MODEL_UNAVAILABLE")


class Counting(ConversationDispatcherPort):
    def __init__(self, inner: ConversationDispatcherPort) -> None:
        self.inner, self.calls = inner, 0

    async def dispatch(
        self, *, method: str, arguments: Json, idempotency_key: str
    ) -> ConversationDispatchOutcome:
        self.calls += 1
        await asyncio.sleep(0)
        return await self.inner.dispatch(
            method=method, arguments=arguments, idempotency_key=idempotency_key
        )


def _handlers(runtime: AppRuntime) -> JudgmentReviewHandlers:
    registry = vars(runtime.bus)["_registry"]
    owner = registry.resolve("hypothesis/review/request").__self__
    assert isinstance(owner, JudgmentReviewHandlers)
    return owner


async def _first_run(tmp_path: Path) -> tuple[AppRuntime, str, str, str]:
    runtime = await setup(tmp_path, ControlledResearchModel(one=True))
    tui = TuiSessionService(
        session_id="test:tui",
        store=SqliteConversationSessionStore(runtime.ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(runtime.bus),
        clock=SystemClock(),
    )
    turn = await tui.execute("LAB-42의 지연과 조건을 알려줘")
    await runtime.bus.drain()
    listed = value(
        await runtime.bus.dispatch(request("hypothesis/list", "hyp", {"project_id": "p"}))
    )
    (item,) = cast(list[Json], listed["hypotheses"])
    return (
        runtime,
        str(turn.response["thread_id"]),
        str(item["hypothesis_id"]),
        str(item["revision_digest"]),
    )


def _payload(thread: str, hypothesis: str, digest: str, **extra: object) -> dict[str, object]:
    return cast(
        dict[str, object],
        {
            "project_id": "p",
            "thread_id": thread,
            "hypothesis_id": hypothesis,
            "hypothesis_revision_digest": digest,
            "reason_codes": ["EVIDENCE_INTERPRETATION"],
            "note": "근거 해석이 다릅니다",
            **extra,
        },
    )


async def _listed(runtime: AppRuntime, key: str) -> list[Json]:
    body = value(
        await runtime.bus.dispatch(request("hypothesis/review/list", key, {"project_id": "p"}))
    )
    return cast(list[Json], body["requests"])


@pytest.mark.asyncio
async def test_request_reaches_the_research_loop_and_is_resolved_when_the_result_is_published(
    tmp_path: Path,
) -> None:
    runtime, thread, hypothesis, digest = await _first_run(tmp_path)
    try:
        body = value(
            await runtime.bus.dispatch(
                request("hypothesis/review/request", "r1", _payload(thread, hypothesis, digest))
            )
        )
        assert body["created"] is True and body["instruction"] == "ACCEPTED"
        record = cast(Json, body["request"])
        assert record["status"] == "REVIEWING" and record["instruction_operation_id"]
        await runtime.bus.drain()
        (settled,) = await _listed(runtime, "l1")
        assert settled["status"] == "RESOLVED"
        resolution = cast(Json, settled["resolution"])
        assert resolution["outcome"] in {"UPHELD", "CHANGED", "HOLD"}
        assert resolution["previous_relation"] and settled["resolution_ref"]
        turns = value(
            await runtime.bus.dispatch(
                request("thread/activity/list", "act", {"project_id": "p", "thread_id": thread})
            )
        )
        assert "재검토 요청" in str(turns)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_revision_keep_a_single_open_request(
    tmp_path: Path,
) -> None:
    runtime, thread, hypothesis, digest = await _first_run(tmp_path)
    try:
        handlers = _handlers(runtime)
        counting = Counting(BusConversationDispatcher(runtime.bus))
        handlers.bind_dispatcher(counting)
        first, second = await asyncio.gather(
            runtime.bus.dispatch(
                request("hypothesis/review/request", "c1", _payload(thread, hypothesis, digest))
            ),
            runtime.bus.dispatch(
                request("hypothesis/review/request", "c2", _payload(thread, hypothesis, digest))
            ),
        )
        one, two = value(first), value(second)
        assert cast(Json, one["request"])["request_id"] == cast(Json, two["request"])["request_id"]
        assert sorted([one["created"], two["created"]]) == [False, True]
        assert counting.calls == 1
        await runtime.bus.drain()
        assert len(await _listed(runtime, "l2")) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_failed_send_keeps_the_request_open_and_the_same_request_is_sent_again(
    tmp_path: Path,
) -> None:
    runtime, thread, hypothesis, digest = await _first_run(tmp_path)
    try:
        handlers = _handlers(runtime)
        real = BusConversationDispatcher(runtime.bus)
        handlers.bind_dispatcher(Failing())
        failed = value(
            await runtime.bus.dispatch(
                request("hypothesis/review/request", "f1", _payload(thread, hypothesis, digest))
            )
        )
        record = cast(Json, failed["request"])
        assert failed["instruction"] == "FAILED" and record["status"] == "OPEN"
        assert record["instruction_failure"] == "MODEL_UNAVAILABLE"
        handlers.bind_dispatcher(real)
        again = value(
            await runtime.bus.dispatch(
                request("hypothesis/review/request", "f2", _payload(thread, hypothesis, digest))
            )
        )
        resent = cast(Json, again["request"])
        assert again["created"] is False and again["instruction"] == "ACCEPTED"
        assert resent["request_id"] == record["request_id"] and resent["status"] == "REVIEWING"
        assert resent["attempts"] == 2
        await runtime.bus.drain()
        assert [item["status"] for item in await _listed(runtime, "l3")] == ["RESOLVED"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_request_for_an_old_revision_is_refused(tmp_path: Path) -> None:
    runtime, thread, hypothesis, _ = await _first_run(tmp_path)
    try:
        refused = await runtime.bus.dispatch(
            request("hypothesis/review/request", "old", _payload(thread, hypothesis, "0" * 64))
        )
        assert refused.error is not None
        assert "REVIEW_HYPOTHESIS_REVISION_CHANGED" in refused.error.message
        assert await _listed(runtime, "l4") == []
    finally:
        runtime.close()
