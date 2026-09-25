from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

FORBIDDEN_CORE_LITERALS = (
    "project:demo-system",
    "object:comparison-gap",
    "action:manifest-compare",
    "Generic integrated R&D system",
    "accepted comparison requires the same dataset version",
)
_CONCRETE_PROJECT_ID = re.compile(r"(?<![A-Za-z0-9_])project:[A-Za-z0-9][A-Za-z0-9._:-]*")


def _hardcoded_project_branch_lines(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.IfExp, ast.While)):
            continue
        for child in ast.walk(node.test):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and _CONCRETE_PROJECT_ID.search(child.value)
            ):
                lines.add(child.lineno)
    return tuple(sorted(lines))


def test_demo_specific_literals_do_not_leak_into_core_or_ui() -> None:
    root = Path(__file__).resolve().parents[2]
    targets = (root / "src", root / "apps" / "web" / "src")
    violations: list[str] = []
    for target in targets:
        for path in target.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for literal in FORBIDDEN_CORE_LITERALS:
                if literal in text:
                    violations.append(f"{path.relative_to(root)}: {literal}")
    assert violations == []


def test_core_has_no_project_id_conditional_branch() -> None:
    root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in (root / "src").rglob("*.py"):
        for line_number in _hardcoded_project_branch_lines(path.read_text(encoding="utf-8")):
            violations.append(f"{path.relative_to(root)}:{line_number}")
    assert violations == []


@pytest.mark.parametrize(
    "source",
    [
        'if project == "project:fixed-id":\n    pass\n',
        'if (\n    project ==\n    "project:fixed-id"\n):\n    pass\n',
        'answer = 1 if project == "project:fixed-id" else 0\n',
    ],
)
def test_concrete_project_id_branch_is_rejected_inline_or_multiline(source: str) -> None:
    assert _hardcoded_project_branch_lines(source)


@pytest.mark.parametrize(
    "source",
    [
        'if isinstance(selection, dict) and selection.get("project_id") != project:\n    pass\n',
        'if work is not None and work.request_ref.project_id == project:\n    pass\n',
        'if span is None or span.project_id != project:\n    pass\n',
        'if project.startswith("project:"):\n    pass\n',
        'if project == "project:":\n    pass\n',
    ],
)
def test_project_variable_and_namespace_prefix_are_not_concrete_ids(source: str) -> None:
    assert _hardcoded_project_branch_lines(source) == ()
