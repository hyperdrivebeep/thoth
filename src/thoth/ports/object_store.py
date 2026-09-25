from __future__ import annotations

from pathlib import Path
from typing import Protocol

from thoth.domain.ids import Sha256


class ObjectStorePort(Protocol):
    def put(self, raw: bytes, expected_digest: Sha256, *, operation_id: str) -> Path: ...

    def read(self, digest: Sha256) -> bytes: ...

    def path_for(self, digest: Sha256) -> Path: ...
