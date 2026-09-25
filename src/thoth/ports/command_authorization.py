from typing import Protocol, runtime_checkable

from pydantic import JsonValue


@runtime_checkable
class CommandAuthorizerPort(Protocol):
    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None: ...
