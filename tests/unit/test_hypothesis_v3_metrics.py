"""The v3 metric script counts tables, unknown rows, refutation conditions and basis links."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.hypothesis_v3_metrics import find_portfolio, main, measure


def _row(hypothesis_id: str, expected: str, basis: list[str] | None = None) -> dict[str, object]:
    return {"hypothesis_id": hypothesis_id, "expected": expected, "basis": basis or []}


def _portfolio() -> dict[str, object]:
    split = {
        "test_id": "t1",
        "expected_by_hypothesis": [_row("h1", "비 옴", ["s1"]), _row("h2", "안 옴", ["s2"])],
    }
    partial = {"test_id": "t2", "expected_by_hypothesis": [_row("h1", " 모름 ")]}
    return {
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "discriminating_tests": [split, partial],
                "refutation_conditions": ["a", "b"],
            },
            {"hypothesis_id": "h2", "discriminating_tests": [{"test_id": "t3"}]},
        ]
    }


def test_counts_come_from_the_tables_and_a_v2_result_counts_zero_rows() -> None:
    got = measure(_portfolio())
    assert got["tests"] == 3 and got["tests_with_table"] == 2
    assert got["countable_tests"] == 1 and got["splitting_tests"] == 1
    assert got["table_rows"] == 3 and got["unknown_ratio"] == 0.333
    assert got["refutation_conditions"] == 2 and got["rows_with_basis_ratio"] == 0.667
    only = {"hypothesis_id": "h1", "discriminating_tests": [{"test_id": "t"}]}
    v2 = measure({"hypotheses": [only]})
    assert v2["table_rows"] == 0 and v2["unknown_ratio"] is None and v2["tests_with_table"] == 0


def test_the_portfolio_is_found_in_a_thread_read_answer(tmp_path: Path) -> None:
    wrapped = {"current_result": {"result": {"portfolio": _portfolio()}}}
    assert find_portfolio(wrapped) is not None and find_portfolio({"x": 1}) is None
    path = tmp_path / "r.json"
    path.write_text(json.dumps(wrapped), encoding="utf-8")
    assert main([str(path)]) == 0
    assert main([str(tmp_path / "missing.json")]) == 1
