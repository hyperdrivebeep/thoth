"""The v3 metric script counts tables, unknown rows, refutation conditions and basis links."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.hypothesis_v3_metrics import (
    fill_report,
    find_portfolio,
    find_table_fill,
    main,
    measure,
    worst_for_item,
)


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


def test_a_prediction_row_without_basis_is_counted() -> None:
    # In _portfolio() the rows either have a basis or are 모름, so none counts.
    assert measure(_portfolio())["unbased_prediction_rows"] == 0
    test = {
        "test_id": "t4",
        "expected_by_hypothesis": [
            _row("h1", "비 옴"),
            _row("h2", "안 옴", ["s9"]),
            _row("h3", "모름"),
            _row("h4", "  모름 "),
        ],
    }
    got = measure({"hypotheses": [{"hypothesis_id": "h1", "discriminating_tests": [test]}]})
    assert got["table_rows"] == 4 and got["unbased_prediction_rows"] == 1
    assert measure({"hypotheses": []})["unbased_prediction_rows"] == 0


RANKING_CASES = Path(__file__).parents[1] / "fixtures" / "hypothesis_eval" / "ranking_cases.json"


def test_worst_matches_the_screen_rule_on_every_shared_ranking_case() -> None:
    # The same cases testRanking.cases.test.ts reads: one rule, two languages, the same numbers.
    cases = json.loads(RANKING_CASES.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 25
    for case in cases:
        candidates = [h["id"] for h in case["hypotheses"] if h.get("elimination") != "REPEATED"]
        for item in case["items"]:
            if item["id"] in case["expected"]["worst"]:
                got, _ = worst_for_item(item, candidates)
                assert got == case["expected"]["worst"][item["id"]], (case["id"], item["id"])


def _table(hypothesis_id: str, labels: dict[str, str]) -> dict[str, object]:
    return {
        "hypothesis_id": hypothesis_id,
        "discriminating_tests": [
            {
                "test_id": f"test:{hypothesis_id}",
                "expected_by_hypothesis": [_row(k, v) for k, v in labels.items()],
            }
        ],
    }


def test_a_split_two_and_two_beats_one_against_the_rest() -> None:
    same = "그대로"
    one_vs_rest = {"h1": "빨라짐", "h2": same, "h3": same, "h4": same}
    split = {"h1": same, "h2": same, "h3": "빨라짐", "h4": "빨라짐"}
    got = measure(
        {
            "hypotheses": [
                _table("h1", one_vs_rest),
                _table("h2", {"h1": same, "h2": "빨라짐", "h3": same, "h4": same}),
                _table("h3", split),
                _table("h4", {"h1": same, "h2": same, "h3": same, "h4": "빨라짐"}),
            ]
        }
    )
    assert got["candidates"] == 4 and got["best_worst"] == 2
    assert got["tests_better_than_one_vs_rest"] == 1
    assert [t["worst"] for t in got["worst_by_test"]] == [3, 3, 2, 3]


def test_every_test_one_against_the_rest_ties_and_none_is_better() -> None:
    # The shape of the 6th live run: five hypotheses, each test one against four.
    ids = ["h1", "h2", "h3", "h4", "h5"]
    hypotheses = [_table(i, {j: ("빨라짐" if j == i else "그대로") for j in ids}) for i in ids]
    got = measure({"hypotheses": hypotheses})
    assert got["candidates"] == 5 and got["best_worst"] == 4
    assert got["tests_better_than_one_vs_rest"] == 0
    assert {t["worst"] for t in got["worst_by_test"]} == {4}


def test_a_test_without_a_full_table_cannot_be_counted_and_an_empty_result_has_no_worst() -> None:
    partial = _table("h1", {"h1": "a", "h2": "b"})  # h3 is missing
    only = {"hypothesis_id": "h2", "discriminating_tests": [{"test_id": "t"}]}
    third = {"hypothesis_id": "h3", "discriminating_tests": []}
    got = measure({"hypotheses": [partial, only, third]})
    assert [(t["worst"], t["counted"]) for t in got["worst_by_test"]] == [(2, False), (2, False)]
    assert got["tests_better_than_one_vs_rest"] == 0
    empty = measure({"hypotheses": []})
    assert empty["candidates"] == 0 and empty["best_worst"] is None
    assert empty["tests_better_than_one_vs_rest"] == 0 and empty["worst_by_test"] == []


def test_labels_with_chinese_characters_are_counted_and_nothing_else_changes() -> None:
    clean = _table("h1", {"h1": "빨라짐", "h2": "그대로"})
    mixed = _table("h2", {"h1": "그대로", "h2": "確認 후 빨라짐"})
    got = measure({"hypotheses": [clean, mixed]})
    assert got["labels_with_hanja"] == 1
    assert measure({"hypotheses": [clean]})["labels_with_hanja"] == 0
    assert measure({"hypotheses": []})["labels_with_hanja"] == 0


def test_labels_with_kana_are_counted_apart_from_chinese_characters() -> None:
    clean = _table("h1", {"h1": "빨라짐", "h2": "그대로"})
    kana = _table("h2", {"h1": "그대로", "h2": "정답 대응また는 과소 집계"})
    han = _table("h3", {"h1": "그대로", "h3": "確認 후 빨라짐"})
    got = measure({"hypotheses": [clean, kana, han]})
    assert got["labels_with_kana"] == 1 and got["labels_with_hanja"] == 1
    assert measure({"hypotheses": [clean]})["labels_with_kana"] == 0
    assert measure({"hypotheses": []})["labels_with_kana"] == 0


def test_foreign_script_fields_count_every_readable_text_with_chinese_characters_or_kana() -> None:
    clean = {
        "hypothesis_id": "h1",
        "statement": "처리 설정이 원인일 수 있다",
        "uncertainty": "아직 확인되지 않았다",
        "missing_evidence": ["시행별 정답"],
        "assumptions": [],
        "refutation_conditions": ["반복해도 같으면 제외한다"],
        "discriminating_tests": [
            {"test_id": "t1", "procedure_candidate": "재처리한다", "expected_by_hypothesis": []}
        ],
    }
    assert measure({"hypotheses": [clean]})["foreign_script_fields"] == 0
    dirty = {
        "hypothesis_id": "h2",
        "statement": "確認된 설정",  # 1
        "uncertainty": "아직 확인되지 않았다",
        "missing_evidence": ["독립した 반복", "시행별 정답", "原本 대조"],  # 2
        "counterevidence_queries": ["まだ 찾는다"],  # 1
        "predicted_observations": ["누락이 반복된다"],
        "assumptions": ["정답이 맞다 的"],  # 1
        "refutation_conditions": ["事前에 정한 반복에서 같으면 제외한다"],  # 1
        "discriminating_tests": [
            {
                "test_id": "t2",
                "procedure_candidate": "独立 재집계",  # 1
                "expected_if_true": "누락",
                "expected_if_alternative": "유지",
                "expected_by_hypothesis": [_row("h2", "体계적 증가")],  # 1
            }
        ],
    }
    got = measure({"hypotheses": [clean, dirty]})
    assert got["foreign_script_fields"] == 8
    assert got["labels_with_hanja"] == 1 and got["labels_with_kana"] == 0


RECORD = {
    "state": "CALLED",
    "reason_code": None,
    "cells": 8,
    "open_before": 5,
    "open_after": 2,
    "changed_cells": 3,
}


def test_the_filling_record_is_found_and_reported_as_shares_of_the_cells() -> None:
    wrapped = {
        "current_result": {"result": {"portfolio": _portfolio(), "hypothesis_table_fill": RECORD}}
    }
    assert find_table_fill(wrapped) == RECORD
    assert find_table_fill({"portfolio": _portfolio()}) is None
    report = fill_report(RECORD)
    assert (report["state"], report["changed_cells"]) == ("CALLED", 3)
    assert (report["open_ratio_before"], report["open_ratio_after"]) == (0.625, 0.25)
    failed = fill_report({"state": "FAILED", "reason_code": "X", "cells": 0})
    assert failed["open_ratio_before"] is None and failed["reason_code"] == "X"


def test_main_prints_the_filling_record_only_when_the_result_has_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with_fill = tmp_path / "a.json"
    plain = tmp_path / "b.json"
    result = {"portfolio": _portfolio()}
    with_fill.write_text(json.dumps({"result": {**result, "hypothesis_table_fill": RECORD}}))
    plain.write_text(json.dumps({"result": result}))
    assert main([str(with_fill), str(plain)]) == 0
    first, second = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert first["table_fill"]["state"] == "CALLED" and "table_fill" not in second


def test_the_katakana_middle_dot_is_not_counted_but_a_kana_letter_is() -> None:
    dot = "\u30fb"
    item = {
        "hypothesis_id": "h1",
        "statement": f"조건{dot}측정계",
        "missing_evidence": [f"조건{dot}측정계", "독립した 반복"],
        "discriminating_tests": [
            {
                "test_id": "t",
                "expected_by_hypothesis": [_row("h1", f"가{dot}나"), _row("h2", "また")],
            }
        ],
    }
    got = measure({"hypotheses": [item]})
    assert got["foreign_script_fields"] == 2 and got["labels_with_kana"] == 1
