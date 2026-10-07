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
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

UNKNOWN_LABEL = "모름"


def _normalized(label: str) -> str:
    return " ".join(label.split()).lower()


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


def measure(portfolio: Mapping[str, Any]) -> dict[str, Any]:
    hypotheses = _maps(portfolio.get("hypotheses"))
    ids = {str(item.get("hypothesis_id")) for item in hypotheses}
    tests = [test for item in hypotheses for test in _maps(item.get("discriminating_tests"))]
    tables = [_maps(test.get("expected_by_hypothesis")) for test in tests]
    rows = [row for table in tables for row in table]
    unknown = [row for row in rows if _normalized(str(row.get("expected", ""))) == UNKNOWN_LABEL]
    with_basis = [row for row in rows if row.get("basis")]
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
        report = {"file": name, **(measure(portfolio) if portfolio else {"error": "NO_PORTFOLIO"})}
        print(json.dumps(report, ensure_ascii=False))
        code = code or (1 if portfolio is None else 0)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
