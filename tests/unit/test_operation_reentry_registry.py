from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.ports.operation import OperationReentryDenied
from thoth.protocol.registry import CommandHandler, MethodRegistry


def test_reentry_callback_is_preserved_when_handler_is_decorated() -> None:
    registry = MethodRegistry()

    async def handler(_value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {}

    def authorizer(operation: OperationRecord) -> None:
        del operation
        raise OperationReentryDenied("CALLER_DENIED")

    def decorate(original: CommandHandler) -> CommandHandler:
        async def wrapped(value: dict[str, JsonValue]):
            return await original(value)

        return wrapped

    registry.register("thread/start", handler)
    registry.register_reentry_authorizer("thread/start", authorizer)
    registry.decorate("thread/start", decorate)
    operation = OperationRecord(
        operation_id="op",
        project_id="p",
        method="thread/start",
        idempotency_key="key",
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(OperationReentryDenied, match="CALLER_DENIED"):
        registry.authorize_reentry(operation)
