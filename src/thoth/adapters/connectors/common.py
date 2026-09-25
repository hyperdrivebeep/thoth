from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from thoth.domain.connectors import ConnectorCheckpoint


def selector_text(selector: dict[str, JsonValue], key: str) -> str:
    value = selector.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"connector selector requires {key}")
    return value


def optional_text(selector: dict[str, JsonValue], key: str) -> str | None:
    value = selector.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"connector selector {key} must be a non-empty string")
    return value


def media_type(path: Path, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(path.name)[0] or fallback


def content_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checkpoint(kind: str, value: str) -> ConnectorCheckpoint:
    return ConnectorCheckpoint(
        kind=kind,
        value=value,
        digest=hashlib.sha256(f"{kind}:{value}".encode()).hexdigest(),
    )


def utc_now() -> datetime:
    return datetime.now(UTC)
