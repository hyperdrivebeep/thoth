"""The journal gate is reentrant, shared and leak-free; result digest bytes are frozen."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from pydantic import JsonValue

from thoth.domain.errors import CanonicalizationError
from thoth.protocol import journal_completion_gate as gates
from thoth.protocol.journal_completion import operation_result_digest

VECTORS = json.loads(
    (Path(__file__).parents[1] / "fixtures/operation_result_digest_vectors.json").read_text(
        encoding="utf-8"
    )
)["vectors"]


@pytest.mark.parametrize("vector", VECTORS)
def test_digest_bytes_match_cp56_vectors(vector: dict[str, object]) -> None:
    from typing import cast

    actual = operation_result_digest(cast(dict[str, JsonValue], vector["value"]))
    assert actual.encode("ascii") == str(vector["digest"]).encode("ascii")


def test_binary_float_still_fails_canonicalization() -> None:
    with pytest.raises(CanonicalizationError, match="binary float"):
        operation_result_digest({"value": 0.25})


def test_reentrant_gate_releases_after_exception() -> None:
    key = ("workspace:gate-unit", "operation:gate-unit")
    entries = vars(gates)["_entries"]
    assert key not in entries
    with pytest.raises(RuntimeError, match="injected"), gates.journal_gate(*key):
        entry = entries[key]
        assert entry.users == 1
        with gates.journal_gate(*key):
            assert entries[key] is entry and entry.users == 2
        assert entry.users == 1
        raise RuntimeError("injected")
    assert key not in entries


def test_two_threads_share_gate_and_remove_last_reference() -> None:
    key = ("workspace:threads", "operation:threads")
    first_entered, second_attempted, release_first, second_entered = (Event() for _ in range(4))
    order: list[str] = []

    def first() -> None:
        with gates.journal_gate(*key):
            first_entered.set()
            assert release_first.wait(timeout=5)
            order.append("first")

    def second() -> None:
        second_attempted.set()
        with gates.journal_gate(*key):
            second_entered.set()
            order.append("second")

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert first_entered.wait(timeout=5)
        b = pool.submit(second)
        try:
            assert second_attempted.wait(timeout=5)
            assert not second_entered.wait(timeout=0.05)
        finally:
            release_first.set()
        a.result(timeout=5)
        b.result(timeout=5)
    assert order == ["first", "second"]
    assert key not in vars(gates)["_entries"]


def test_same_operation_in_other_workspace_uses_a_different_gate() -> None:
    with (
        gates.journal_gate("workspace:a", "operation:same"),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):

        def other() -> str:
            with gates.journal_gate("workspace:b", "operation:same"):
                return "entered"

        assert pool.submit(other).result(timeout=5) == "entered"
    assert not {key for key in vars(gates)["_entries"] if key[1] == "operation:same"}
