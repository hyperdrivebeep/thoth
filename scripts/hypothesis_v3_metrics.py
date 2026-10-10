"""Read-only: count what a hypothesis result gives under the v2 or v3 contract (no model call).

Usage:
    python scripts/hypothesis_v3_metrics.py result.json [more.json ...]

The input is a result JSON that holds a hypothesis portfolio: the portfolio itself,
`{"portfolio": ...}`, `{"result": {"portfolio": ...}}` or a `thread/read` answer with
`current_result.result.portfolio`. A v2 result has no expected-result tables, so its table
counts are zero; that is the comparison point.

The test order on the screen (the READY group) is a web rule and is not ported here.
`countable_tests` is the part of it that comes from the result alone: tests whose table names
every hypothesis. Whether such a test also lands in the first group depends on its cost and
executability, which the screen shows.

unbased_prediction_rows counts table rows that give a prediction (not "모름") but name no
supporting span: predictions written from the hypothesis alone ("근거 없는 예측 행 수").

worst, best_worst and tests_better_than_one_vs_rest use the same rule as the screen's test order
(apps/web/src/components/testRanking.ts, outcomeClasses): the result classes a test can give are
the groups of hypotheses with the same label ("모름" stays in every class), and worst is the
size of the largest group. A test whose table does not name every hypothesis cannot be counted;
its worst is candidates - 1 (at least 1). candidates is the number of hypotheses.
tests_better_than_one_vs_rest counts tests whose worst is below candidates - 1, so a test that
splits the hypotheses more evenly than one against the rest.

table_fill is printed when the result carries a hypothesis_table_fill record (v3 only): whether
the table-filling call was made (CALLED, SKIPPED_FULL, SKIPPED_BUDGET, FAILED), why not, how many
cells changed, and the share of cells still open (missing or 모름) before and after it. A cell is
a (test, hypothesis) pair, so these shares differ from unknown_ratio, which counts only rows.

foreign_script_fields counts every readable text of the hypotheses and their tests (statement,
uncertainty, the list texts, refutation conditions, test procedure and expected texts, table
labels) that has Chinese characters or kana; a quotation from the material counts too, so a few
can be right.

labels_with_hanja counts table rows whose label contains Chinese characters (hanja); a Korean
question should get Hangul labels. labels_with_kana counts rows whose label contains Japanese
kana. Labels are only counted, never changed.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

UNKNOWN_LABEL = "모름"
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3040-\u30fa\u30fc-\u30ff]")  # not U+30FB, the katakana middle dot
READABLE_LISTS = (
    "missing_evidence",
    "counterevidence_queries",
    "predicted_observations",
    "assumptions",
    "refutation_conditions",
)
READABLE_TEST_TEXTS = ("procedure_candidate", "expected_if_true", "expected_if_alternative")


def readable_texts(hypothesis: Mapping[str, Any]) -> list[str]:
    """Every text of a hypothesis and its tests that a person reads."""
    found = [str(hypothesis.get(key) or "") for key in ("statement", "uncertainty")]
    for key in READABLE_LISTS:
        found.extend(str(item) for item in cast("list[object]", hypothesis.get(key) or []))
    for test in _maps(hypothesis.get("discriminating_tests")):
        found.extend(str(test.get(key) or "") for key in READABLE_TEST_TEXTS)
        found.extend(
            str(row.get("expected") or "") for row in _maps(test.get("expected_by_hypothesis"))
        )
    return found


def _normalized(label: str) -> str:
    return " ".join(label.split()).lower()


def _outcome_classes(
    item: Mapping[str, Any], candidates: list[str], tester: str | None
) -> list[list[str]] | None:
    """The candidates left under each possible result; None when the split cannot be counted."""
    if item.get("kind") != "TEST" or tester is None or tester not in candidates:
        return None
    labels = item.get("labels")
    if labels is not None:
        if not all(name in labels for name in candidates):
            return None
        unknown = [name for name in candidates if _normalized(labels[name]) == UNKNOWN_LABEL]
        by_label: dict[str, list[str]] = {}
        for name in candidates:
            if name not in unknown:
                by_label.setdefault(_normalized(labels[name]), []).append(name)
        return (
            [unknown] if not by_label else [[*members, *unknown] for members in by_label.values()]
        )
    table = item.get("predictions")
    others = [name for name in candidates if name != tester]
    if table is None or not all(name in table for name in others):
        return None
    return [
        [tester, *[name for name in others if table[name] == "WITH_TEST"]],
        [name for name in others if table[name] == "WITH_ALTERNATIVE"],
    ]


def worst_for_item(item: Mapping[str, Any], candidates: list[str]) -> tuple[int, bool]:
    """(worst, counted): the most candidates left whichever result comes, as the screen counts."""
    classes = _outcome_classes(item, candidates, cast("str | None", item.get("hypothesisId")))
    if classes is None:
        return max(1, len(candidates) - 1), False
    return max(len(members) for members in classes), True


def _maps(value: object) -> list[dict[str, Any]]:
    """The mapping items of a JSON list; anything else is an empty list."""
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast("dict[str, Any]", item) for item in items if isinstance(item, dict)]


def _mapping(value: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def find_portfolio(document: object) -> dict[str, Any] | None:
    """The portfolio inside a result document, or None when there is none."""
    paths = (("portfolio",), ("result", "portfolio"), ("current_result", "result", "portfolio"))
    for path in ((), *paths):
        node = _mapping(document)
        for key in path:
            node = _mapping(node.get(key)) if node is not None else None
        if node is not None and isinstance(node.get("hypotheses"), list):
            return node
    return None


def find_table_fill(document: object) -> dict[str, Any] | None:
    """The table-filling record inside a result document (v3 only), or None when there is none."""
    paths = (("result",), ("current_result", "result"), ())
    for path in paths:
        node = _mapping(document)
        for key in path:
            node = _mapping(node.get(key)) if node is not None else None
        found = None if node is None else _mapping(node.get("hypothesis_table_fill"))
        if found is not None:
            return found
    return None


def fill_report(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the table-filling call did: whether it was made, how many cells changed, and the share
    of cells still open (missing or 모름) before and after it."""
    cells = int(record.get("cells") or 0)
    before, after = int(record.get("open_before") or 0), int(record.get("open_after") or 0)
    return {
        "state": record.get("state"),
        "reason_code": record.get("reason_code"),
        "cells": cells,
        "changed_cells": int(record.get("changed_cells") or 0),
        "open_before": before,
        "open_after": after,
        "open_ratio_before": round(before / cells, 3) if cells else None,
        "open_ratio_after": round(after / cells, 3) if cells else None,
    }


def measure(portfolio: Mapping[str, Any]) -> dict[str, Any]:
    hypotheses = _maps(portfolio.get("hypotheses"))
    ids = {str(item.get("hypothesis_id")) for item in hypotheses}
    tests = [test for item in hypotheses for test in _maps(item.get("discriminating_tests"))]
    tables = [_maps(test.get("expected_by_hypothesis")) for test in tests]
    rows = [row for table in tables for row in table]
    unknown = [row for row in rows if _normalized(str(row.get("expected", ""))) == UNKNOWN_LABEL]
    with_basis = [row for row in rows if row.get("basis")]
    unbased = [row for row in rows if row not in unknown and not row.get("basis")]
    hanja_labels = [row for row in rows if _HAN.search(str(row.get("expected", "")))]
    kana_labels = [row for row in rows if _KANA.search(str(row.get("expected", "")))]
    countable = [
        table
        for table in tables
        if table and {str(row.get("hypothesis_id")) for row in table} >= ids
    ]
    splitting = [
        table
        for table in countable
        if len({_normalized(str(row.get("expected", ""))) for row in table} - {UNKNOWN_LABEL}) >= 2
    ]
    conditions = sum(
        len(cast("list[object]", item.get("refutation_conditions") or [])) for item in hypotheses
    )
    candidates = [str(item.get("hypothesis_id")) for item in hypotheses]
    worst_by_test: list[dict[str, Any]] = []
    for item in hypotheses:
        for test in _maps(item.get("discriminating_tests")):
            rows_of = _maps(test.get("expected_by_hypothesis"))
            labels = {
                str(row.get("hypothesis_id")): str(row.get("expected", "")) for row in rows_of
            }
            worst, counted = worst_for_item(
                {
                    "kind": "TEST",
                    "hypothesisId": str(item.get("hypothesis_id")),
                    "labels": labels if rows_of else None,
                },
                candidates,
            )
            worst_by_test.append(
                {"test_id": test.get("test_id"), "worst": worst, "counted": counted}
            )
    return {
        "hypotheses": len(hypotheses),
        "tests": len(tests),
        "tests_with_table": sum(1 for table in tables if table),
        "countable_tests": len(countable),
        "splitting_tests": len(splitting),
        "table_rows": len(rows),
        "unknown_ratio": round(len(unknown) / len(rows), 3) if rows else None,
        "refutation_conditions": conditions,
        "rows_with_basis_ratio": round(len(with_basis) / len(rows), 3) if rows else None,
        "unbased_prediction_rows": len(unbased),
        "labels_with_hanja": len(hanja_labels),
        "labels_with_kana": len(kana_labels),
        "foreign_script_fields": sum(
            1
            for item in hypotheses
            for text in readable_texts(item)
            if _HAN.search(text) or _KANA.search(text)
        ),
        "candidates": len(candidates),
        "best_worst": min((entry["worst"] for entry in worst_by_test), default=None),
        "tests_better_than_one_vs_rest": sum(
            1 for entry in worst_by_test if entry["worst"] < len(candidates) - 1
        ),
        "worst_by_test": worst_by_test,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    code = 0
    for name in argv:
        try:
            document = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(json.dumps({"file": name, "error": type(error).__name__}, ensure_ascii=False))
            code = 1
            continue
        portfolio = find_portfolio(document)
        report: dict[str, Any] = {
            "file": name,
            **(measure(portfolio) if portfolio else {"error": "NO_PORTFOLIO"}),
        }
        record = find_table_fill(document)
        if record is not None:
            report["table_fill"] = fill_report(record)
        print(json.dumps(report, ensure_ascii=False))
        code = code or (1 if portfolio is None else 0)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
