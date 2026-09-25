from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_projectpack_application_modules_do_not_import_adapters() -> None:
    paths = (
        ROOT / "src/thoth/application/workflows/projectpack_run.py",
        ROOT / "src/thoth/application/commands/projectpacks.py",
    )
    violations = {
        path.relative_to(ROOT).as_posix(): tuple(
            item for item in _imports(path) if item.startswith("thoth.adapters")
        )
        for path in paths
    }
    assert violations == {path.relative_to(ROOT).as_posix(): () for path in paths}


def test_projectpack_command_has_no_provider_name_conditional() -> None:
    path = ROOT / "src/thoth/application/commands/projectpacks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    provider_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in {"codex-oauth", "scripted"}
    }
    assert provider_literals == set()


def test_sqlite_ledger_does_not_reference_create_all() -> None:
    path = ROOT / "src/thoth/adapters/storage/sqlite.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_all"
    ]
    assert calls == []
