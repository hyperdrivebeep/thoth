from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from thoth.domain.ids import Sha256
from thoth.ports.object_store import ObjectStorePort


class ContentAddressedObjectStore(ObjectStorePort):
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._object_root = self._workspace / "objects" / "sha256"
        self._temp_root = self._workspace / "tmp"

    def put(self, raw: bytes, expected_digest: Sha256, *, operation_id: str) -> Path:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_digest.lower():
            raise ValueError("object bytes do not match expected digest")
        destination = self.path_for(expected_digest)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != actual:
                raise RuntimeError("content-addressed object path contains different bytes")
            return destination

        operation_temp = self._temp_root / _safe_segment(operation_id)
        operation_temp.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=operation_temp, delete=False) as staged:
            staged.write(raw)
            staged.flush()
            os.fsync(staged.fileno())
            staged_path = Path(staged.name)
        try:
            os.replace(staged_path, destination)
        finally:
            if staged_path.exists():
                staged_path.unlink()
            with suppress(OSError):
                operation_temp.rmdir()
        return destination

    def read(self, digest: Sha256) -> bytes:
        raw = self.path_for(digest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest.lower():
            raise RuntimeError("stored object failed digest verification")
        return raw

    def path_for(self, digest: Sha256) -> Path:
        normalized = digest.lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("object digest must be a SHA-256 hex string")
        return self._object_root / normalized[:2] / normalized


def _safe_segment(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_." else "_" for character in value
    )
    return normalized[:180] or "operation"
