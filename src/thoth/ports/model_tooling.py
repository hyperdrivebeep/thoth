"""Install the helper programs a model account route needs, without naming any provider."""

from __future__ import annotations

from typing import Protocol


class ModelToolingError(RuntimeError):
    """A typed, user-showable reason; never carries installer output."""


class ModelToolingPort(Protocol):
    def install(self, tool_id: str) -> dict[str, object]: ...
