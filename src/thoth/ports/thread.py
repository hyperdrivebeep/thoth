from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from thoth.domain.project import WorkThread


class ThreadEntryPort(Protocol):
    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None: ...

    async def analyze(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


class ThreadAlreadyExistsError(ValueError):
    pass


class ThreadStorePort(Protocol):
    def create(self, thread: WorkThread) -> None: ...

    def update(self, thread: WorkThread, *, expected_revision: int | None = None) -> bool: ...

    def read(self, thread_id: str) -> WorkThread | None: ...

    def list(self, project_id: str) -> tuple[WorkThread, ...]: ...
