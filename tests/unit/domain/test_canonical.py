from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thoth.domain.canonical import (
    canonical_payload,
    domain_digest,
    head_set_digest,
    normalize_timestamp,
)
from thoth.domain.errors import CanonicalizationError


def test_canonical_same_input_same_bytes_100_runs() -> None:
    payload = {"z": [3, 2, 1], "a": "근거", "when": datetime(2026, 8, 30, tzinfo=UTC)}
    outputs = {canonical_payload(payload) for _ in range(100)}
    assert len(outputs) == 1


def test_unicode_nfc_equivalence() -> None:
    composed = "é"
    decomposed = "e\u0301"
    assert canonical_payload({"value": composed}) == canonical_payload({"value": decomposed})


def test_list_order_is_preserved_but_declared_set_path_is_sorted() -> None:
    left = {"items": ["b", "a", "a"]}
    right = {"items": ["a", "b"]}
    assert canonical_payload(left) != canonical_payload(right)
    set_paths = frozenset({("items",)})
    assert canonical_payload(left, set_paths=set_paths) == canonical_payload(
        right, set_paths=set_paths
    )


def test_decimal_is_canonical_string_and_float_is_rejected() -> None:
    assert canonical_payload({"value": Decimal("1.20")}) == b'{"value":"1.20"}'
    with pytest.raises(CanonicalizationError, match="binary float"):
        canonical_payload({"value": 1.2})


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="naive datetime"):
        normalize_timestamp(datetime(2026, 8, 30))


def test_domain_separator_changes_digest() -> None:
    payload = canonical_payload({"same": "payload"})
    assert domain_digest("SNAPSHOT", "1.0.0", payload) != domain_digest("RECEIPT", "1.0.0", payload)


def test_head_set_digest_ignores_mapping_insertion_order() -> None:
    first = {"project:a": "a" * 64, "thread:b": "b" * 64}
    second = {"thread:b": "b" * 64, "project:a": "a" * 64}
    assert head_set_digest(first) == head_set_digest(second)
