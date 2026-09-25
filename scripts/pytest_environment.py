"""Explicit environment selection for disabled development Hooks, not product gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "config/inactive-development-hook-tests.json"


def inactive_hook_nodeids(root: Path) -> frozenset[str]:
    policy = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if policy.get("schema_version") != "1.0.0" or policy.get("hook_config") != ".codex/hooks.json":
        raise ValueError("INVALID_HOOK_TEST_SCOPE_POLICY")
    nodeids = policy.get("nodeids")
    if (
        not isinstance(nodeids, list)
        or len(nodeids) != len(set(nodeids))
        or any(
            not isinstance(node, str)
            or not node.startswith("tests/architecture/")
            or "::" not in node
            or "\\" in node.partition("::")[0]
            or ".." in node.partition("::")[0]
            for node in nodeids
        )
    ):
        raise ValueError("INVALID_HOOK_TEST_SCOPE_NODEIDS")
    hooks = json.loads((root / policy["hook_config"]).read_text(encoding="utf-8-sig"))
    if not isinstance(hooks.get("hooks"), dict):
        raise ValueError("HOOK_CONFIGURATION_UNAVAILABLE")
    return frozenset(nodeids) if hooks["hooks"] == {} else frozenset()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--include-inactive-hooks",
        action="store_true",
        default=False,
        help="Explicitly include disabled development Hook diagnostics.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--include-inactive-hooks"):
        return
    try:
        excluded = inactive_hook_nodeids(ROOT)
    except (OSError, ValueError, TypeError) as exc:
        raise pytest.UsageError(f"Cannot establish active test scope: {exc}") from exc
    deselected = [item for item in items if item.nodeid in excluded]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item.nodeid not in excluded]
        reporter: Any = config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                f"THOTH_TEST_SCOPE: {len(deselected)} inactive development Hook tests deselected; "
                f"{len(items)} active cases retained."
            )
