from __future__ import annotations

from pydantic import JsonValue

from thoth.domain.conversation import ConversationDispatchOutcome
from thoth.ports.conversation import ConversationDispatcherPort
from thoth.protocol.bus import READ_QUERY_METHODS, CommandBus
from thoth.protocol.jsonrpc import JsonRpcRequest


class BusConversationDispatcher(ConversationDispatcherPort):
    def __init__(self, bus: CommandBus) -> None:
        self._bus = bus

    async def dispatch(
        self,
        *,
        method: str,
        arguments: dict[str, JsonValue],
        idempotency_key: str,
    ) -> ConversationDispatchOutcome:
        dispatch = self._bus.query if method in READ_QUERY_METHODS else self._bus.dispatch
        response = await dispatch(
            JsonRpcRequest.model_validate(
                {
                    "id": idempotency_key,
                    "method": method,
                    "params": {
                        "_meta": {"idempotencyKey": idempotency_key},
                        "input": arguments,
                    },
                }
            )
        )
        if response.error is not None:
            return ConversationDispatchOutcome(
                success=False,
                error_code=response.error.code,
                error_message=response.error.message,
            )
        assert response.result is not None
        value = response.result.get("value")
        return ConversationDispatchOutcome(
            success=True,
            value={} if not isinstance(value, dict) else value,
        )
