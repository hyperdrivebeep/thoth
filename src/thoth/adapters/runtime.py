from __future__ import annotations

import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdGenerator:
    def new(self, prefix: str) -> str:
        normalized = prefix.strip().lower().replace("_", "-")
        return f"{normalized}:{uuid.uuid4()}"
