from datetime import UTC, datetime
from typing import Any

import pytest

from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.domain.conversation import ConversationDispatchOutcome, TuiSessionState, TuiTurnStatus
from thoth.ports.conversation import ConversationSessionStorePort


class MemoryStore(ConversationSessionStorePort):
    def __init__(self, session: TuiSessionState) -> None:
        self.session = session

    def read(self, session_id: str) -> TuiSessionState | None:
        return self.session if self.session.session_id == session_id else None

    def put(self, value: TuiSessionState) -> None:
        self.session = value


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 17, tzinfo=UTC)


class Dispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(self, *, method: str, arguments: dict[str, Any], idempotency_key: str):
        del idempotency_key
        self.calls.append((method, arguments))
        if method == "thread/read":
            return ConversationDispatchOutcome(
                success=True,
                value={
                    "request": {
                        "effective_question": "same question",
                        "request_epoch": 1,
                        "operation_id": "op:1",
                        "model_settings": {
                            "provider": "codex-oauth",
                            "model": "gpt-5.6-terra",
                            "reasoning_effort": "high",
                        },
                    }
                },
            )
        if method == "model/settings/read":
            return ConversationDispatchOutcome(
                success=True,
                value={
                    "effective_settings": {
                        "provider": "xai",
                        "model": "grok-4.6",
                        "reasoning_effort": "high",
                    }
                },
            )
        raise AssertionError(method)


@pytest.mark.asyncio
async def test_retry_holds_when_current_model_differs_from_stored_request() -> None:
    session = TuiSessionState(
        session_id="tui:retry",
        active_project_id="project:tui",
        active_thread_id="thread:tui",
        revision=1,
        updated_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    dispatcher = Dispatcher()
    service = TuiSessionService(
        session_id=session.session_id,
        store=MemoryStore(session),
        router=ConversationRouter(),
        dispatcher=dispatcher,
        clock=Clock(),
    )
    result = await service.execute("/retry")
    assert result.status == TuiTurnStatus.HOLD
    assert result.error_message == "RETRY_MODEL_SELECTION_CHANGED"
    assert [method for method, _ in dispatcher.calls] == ["thread/read", "model/settings/read"]


class MatchingDispatcher(Dispatcher):
    def __init__(self, provider: str, model: str, effort: str, capability_source: str) -> None:
        super().__init__()
        self.provider, self.model, self.effort = provider, model, effort
        self.capability_source = capability_source

    async def dispatch(self, *, method: str, arguments: dict[str, Any], idempotency_key: str):
        del idempotency_key
        self.calls.append((method, arguments))
        if method == "thread/read":
            return ConversationDispatchOutcome(
                success=True,
                value={
                    "request": {
                        "effective_question": "same question",
                        "request_epoch": 1,
                        "operation_id": "op:1",
                        "model_settings": {
                            "provider": self.provider,
                            "model": self.model,
                            "reasoning_effort": self.effort,
                        },
                    }
                },
            )
        if method == "model/settings/read":
            return ConversationDispatchOutcome(
                success=True,
                value={
                    "effective_settings": {
                        "provider": self.provider,
                        "model": self.model,
                        "reasoning_effort": self.effort,
                        "capability_source": self.capability_source,
                    }
                },
            )
        if method == "thread/input":
            return ConversationDispatchOutcome(success=True, value={"thread_id": "thread:tui"})
        raise AssertionError(method)


def _session() -> TuiSessionState:
    return TuiSessionState(
        session_id="tui:retry",
        active_project_id="project:tui",
        active_thread_id="thread:tui",
        revision=1,
        updated_at=datetime(2026, 9, 17, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_retry_matching_xai_omits_codex_policy() -> None:
    dispatcher = MatchingDispatcher(
        "xai", "grok-4.6", "high", "omo-xai-models-store/openai-responses-v1"
    )
    service = TuiSessionService(
        session_id="tui:retry",
        store=MemoryStore(_session()),
        router=ConversationRouter(),
        dispatcher=dispatcher,
        clock=Clock(),
    )
    result = await service.execute("/retry")
    assert result.status == TuiTurnStatus.DISPATCHED, result
    input_calls = [arguments for method, arguments in dispatcher.calls if method == "thread/input"]
    assert len(input_calls) == 1
    assert "retry_policy" not in input_calls[0]


@pytest.mark.asyncio
async def test_retry_matching_codex_keeps_once_transient_policy() -> None:
    dispatcher = MatchingDispatcher(
        "codex-oauth", "gpt-5.6-terra", "high", "codex-local-catalog/direct-responses-v1"
    )
    service = TuiSessionService(
        session_id="tui:retry",
        store=MemoryStore(_session()),
        router=ConversationRouter(),
        dispatcher=dispatcher,
        clock=Clock(),
    )
    result = await service.execute("/retry")
    assert result.status == TuiTurnStatus.DISPATCHED, result
    input_calls = [arguments for method, arguments in dispatcher.calls if method == "thread/input"]
    assert input_calls[0]["retry_policy"] == "ONCE_TRANSIENT_429"
