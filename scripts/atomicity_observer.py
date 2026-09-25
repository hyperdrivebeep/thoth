"""Observe actual callable entry per pytest node; assertions remain in ordinary tests.

Only enabled by the dedicated owner runner. No model/DB calls, state injection or
test selection. Child-process crash behavior is established by the parent's
explicit durable-state assertions, not inferred from this in-process observer.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import CodeType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_current: str | None = None
_observed: dict[str, set[str]] = {}
_symbols: dict[tuple[str, str], str] = {}
_monitor: Any = None
_tool_id = 4


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--atomicity-observations", default=None)


def _start(code: CodeType, offset: int) -> Any:
    symbol = _symbols.get((code.co_filename.replace("\\", "/").casefold(), code.co_qualname))
    if symbol is None:
        # This immutable watch list never records stdlib/third-party code. Stop
        # receiving repeated starts for those code objects, not for watched code.
        return _monitor.DISABLE
    if _current is not None:
        _observed[_current].add(symbol)
    return None


def pytest_sessionstart(session: pytest.Session) -> None:
    global _monitor
    if not session.config.getoption("--atomicity-observations"):
        return
    inventory = json.loads((ROOT / "config/atomicity-paths.json").read_text(encoding="utf-8"))
    discovery = json.loads((ROOT / inventory["discovery"]).read_text(encoding="utf-8"))
    for row in discovery["symbols"]:
        path = row["source"]
        module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        # Scanner records the lexical class/function path independently of line numbers.
        qualname = row["symbol"]
        _symbols[(str(ROOT / path).replace("\\", "/").casefold(), qualname)] = (
            module + "." + qualname
        )
    for path in (ROOT / "migrations/versions").glob("*.py"):
        module = path.relative_to(ROOT).as_posix().removesuffix(".py").replace("/", ".")
        for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
            if isinstance(node, ast.FunctionDef):
                _symbols[(str(path).replace("\\", "/").casefold(), node.name)] = (
                    module + "." + node.name
                )
    _monitor = getattr(sys, "monitoring", None)
    if _monitor is None or _monitor.get_tool(_tool_id) is not None:
        raise pytest.UsageError(
            "Atomicity observation requires an available Python monitoring slot"
        )
    _monitor.use_tool_id(_tool_id, "thoth-atomicity")
    _monitor.register_callback(_tool_id, _monitor.events.PY_START, _start)
    _monitor.set_events(_tool_id, _monitor.events.PY_START)


def observe_validated_child_stack(symbols: list[str]) -> None:
    """Called after the crash test validates its durable child marker and recovery assertions."""
    if _current is not None and _monitor is not None:
        _observed[_current].update(s for s in symbols if s.startswith("thoth."))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    global _current
    _current = item.nodeid
    _observed.setdefault(_current, set())
    try:
        yield
    finally:
        _current = None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    destination = session.config.getoption("--atomicity-observations")
    if not destination:
        return
    if _monitor is not None:
        _monitor.set_events(_tool_id, 0)
        _monitor.register_callback(_tool_id, _monitor.events.PY_START, None)
        _monitor.free_tool_id(_tool_id)
    Path(destination).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "exit_code": int(exitstatus),
                "nodes": {k: sorted(v) for k, v in sorted(_observed.items())},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
