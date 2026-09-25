from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class IdGeneratorPort(Protocol):
    def new(self, prefix: str) -> str: ...


class AtomicUnitOfWorkPort(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...
