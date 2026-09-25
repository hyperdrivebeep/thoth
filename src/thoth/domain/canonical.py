from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import cast

from pydantic import BaseModel

from thoth.domain.errors import CanonicalizationError
from thoth.domain.ids import Sha256

SetPath = tuple[str | int, ...]


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalizationError("naive datetime is not canonical")
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_key(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize(value: object, path: SetPath, set_paths: frozenset[SetPath]) -> object:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"), path, set_paths)
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        raise CanonicalizationError("binary float is prohibited in canonical domain state")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return normalize_timestamp(value)
    if isinstance(value, Enum):
        return _normalize(value.value, path, set_paths)
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        normalized: dict[str, object] = {}
        for raw_key, raw_value in mapping.items():
            if not isinstance(raw_key, str):
                raise CanonicalizationError("canonical object keys must be strings")
            key = normalize_text(raw_key)
            normalized[key] = _normalize(raw_value, (*path, key), set_paths)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, Set) and not isinstance(value, str | bytes | bytearray):
        values = cast(Set[object], value)
        items = [_normalize(item, (*path, 0), set_paths) for item in values]
        unique = {_canonical_key(item): item for item in items}
        return [unique[key] for key in sorted(unique)]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        values = cast(Sequence[object], value)
        items = [_normalize(item, (*path, index), set_paths) for index, item in enumerate(values)]
        if path in set_paths:
            unique = {_canonical_key(item): item for item in items}
            return [unique[key] for key in sorted(unique)]
        return items
    raise CanonicalizationError(f"unsupported canonical type: {type(value).__name__}")


def canonical_payload(
    value: BaseModel | Mapping[str, object],
    *,
    set_paths: frozenset[SetPath] = frozenset(),
) -> bytes:
    normalized = _normalize(value, (), set_paths)
    return _canonical_key(normalized)


def domain_digest(domain: str, schema_version: str, payload: bytes) -> Sha256:
    if not domain or not schema_version:
        raise CanonicalizationError("domain and schema_version are required")
    prefix = f"THOTH/{domain}/{schema_version}\0".encode()
    return hashlib.sha256(prefix + payload).hexdigest()


def model_digest(
    domain: str,
    model: BaseModel,
    *,
    schema_version: str,
    set_paths: frozenset[SetPath] = frozenset(),
) -> Sha256:
    return domain_digest(
        domain,
        schema_version,
        canonical_payload(model, set_paths=set_paths),
    )


def head_set_digest(heads: Mapping[str, Sha256], *, schema_version: str = "1.0.0") -> Sha256:
    return domain_digest("HEAD_SET", schema_version, canonical_payload(dict(heads)))
