"""Admission is an explicit nonterminal handler result, distinct from command success."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import JsonValue

from thoth.domain.operation import OperationRecord

current_operation: ContextVar[OperationRecord | None] = ContextVar(
    "current_operation", default=None
)


@dataclass(frozen=True)
class PendingExecution:
    value: dict[str, JsonValue]


@dataclass(frozen=True)
class AcceptedRunning:
    value: dict[str, JsonValue]
    continue_execution: Callable[[], Awaitable[dict[str, JsonValue] | PendingExecution]]
    target_operation: OperationRecord | None = None
