from __future__ import annotations

import ast
from pathlib import Path

from apps.api.main import app, healthz

from thoth import __version__


def test_version_is_explicit() -> None:
    assert __version__ == "0.1.0"


def test_health_endpoint() -> None:
    assert healthz() == {"status": "ok", "version": __version__}
    assert any(getattr(route, "path", None) == "/healthz" for route in app.routes)


def test_domain_and_application_do_not_import_frameworks() -> None:
    forbidden = {"fastapi", "sqlalchemy", "openai", "uvicorn", "typer", "react"}
    repo_root = Path(__file__).resolve().parents[1]
    roots = [repo_root / "src" / "thoth" / "domain", repo_root / "src" / "thoth" / "application"]
    violations: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = {node.module.split(".")[0]}
                else:
                    continue
                blocked = names & forbidden
                if blocked:
                    violations.append(f"{path.relative_to(repo_root)}: {sorted(blocked)}")
    assert violations == []
