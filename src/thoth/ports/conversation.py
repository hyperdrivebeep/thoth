from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from thoth.domain.conversation import ConversationDispatchOutcome, TuiSessionState


class ConversationSessionStorePort(Protocol):
    def read(self, session_id: str) -> TuiSessionState | None: ...
    def put(self, value: TuiSessionState) -> None: ...


class ConversationDispatcherPort(Protocol):
    async def dispatch(
        self,
        *,
        method: str,
        arguments: dict[str, JsonValue],
        idempotency_key: str,
    ) -> ConversationDispatchOutcome: ...
