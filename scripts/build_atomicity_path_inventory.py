"""Discover registrations and writer candidates; discovery never certifies atomicity."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import inspect
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WRITE_NAMES = frozenset(
    {
        "insert",
        "update",
        "delete",
        "execute",
        "exec_driver_sql",
        "save",
        "commit",
        "persist",
        "put",
        "append",
        "journal",
        "publish",
        "create",
        "upsert",
        "write_connection",
    }
)


class Symbols(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self.path, self.source = path, source
        self.scope: list[str] = []
        self.symbols: list[dict[str, Any]] = []
        self.registrations: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = ".".join([*self.scope, node.name])
        calls = sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)})
        writes = [
            c
            for c in calls
            if c.rsplit(".", 1)[-1] in WRITE_NAMES
            or c.rsplit(".", 1)[-1].startswith(("_persist", "put_", "save_", "commit_"))
        ]
        text = ast.get_source_segment(self.source, node) or ""
        self.symbols.append(
            {
                "symbol": name,
                "source": self.path,
                "line": node.lineno,
                "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "calls": calls,
                "writer_candidates": writes,
                "direct_sql_write": any(
                    isinstance(n, ast.Call)
                    and (
                        (
                            isinstance(n.func, ast.Name)
                            and n.func.id in {"insert", "update", "delete"}
                        )
                        or (
                            isinstance(n.func, ast.Attribute)
                            and n.func.attr == "exec_driver_sql"
                            and any(
                                isinstance(a, ast.Constant)
                                and isinstance(a.value, str)
                                and a.value.lstrip()
                                .upper()
                                .startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
                                for a in n.args
                            )
                        )
                    )
                    for n in ast.walk(node)
                ),
                "await_count": sum(isinstance(n, ast.Await) for n in ast.walk(node)),
                "transaction_scopes": sorted(
                    {
                        ast.unparse(i.context_expr)
                        for n in ast.walk(node)
                        if isinstance(n, (ast.With, ast.AsyncWith))
                        for i in n.items
                        if "transaction" in ast.unparse(i.context_expr)
                        or ".atomic(" in ast.unparse(i.context_expr)
                    }
                ),
            }
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = function
    visit_AsyncFunctionDef = function

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"register", "decorate"}:
            receiver = ast.unparse(node.func.value)
            if receiver == "registry" and len(node.args) >= 2:
                method = node.args[0]
                value = method.value if isinstance(method, ast.Constant) else None
                self.registrations.append(
                    {
                        "method": value if isinstance(value, str) else None,
                        "method_expression": ast.unparse(method),
                        "binding_expression": ast.unparse(node.args[1]),
                        "kind": node.func.attr,
                        "source": self.path,
                        "line": node.lineno,
                        "caller": ".".join(self.scope),
                    }
                )
        self.generic_visit(node)


def static_inventory(root: Path = ROOT) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    registrations: list[dict[str, Any]] = []
    files = []
    for path in sorted((root / "src/thoth").rglob("*.py")):
        if not any(part in {"application", "apps", "adapters", "protocol"} for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        visitor = Symbols(relative, source)
        visitor.visit(ast.parse(source))
        symbols.extend(visitor.symbols)
        registrations.extend(visitor.registrations)
        files.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {
        "schema_version": "1.0.0",
        "purpose": "DISCOVERY_NOT_CLOSURE",
        "files": files,
        "symbols": symbols,
        "registrations": registrations,
        "unresolved_registrations": [r for r in registrations if r["method"] is None],
    }


def registered_bindings(
    *, call_graph: bool = False, configured: bool = False
) -> list[dict[str, Any]]:
    # Controlled empty local workspace: constructs providers, never dispatches their I/O.
    from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
    from thoth.apps.runtime import create_runtime

    result: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="thoth-atomicity-inventory-") as directory:
        runtime = create_runtime(
            Path(directory), evaluation_catalog=FrozenEvaluationCatalog(()) if configured else None
        )
        try:
            registry = runtime.bus._registry
            for method in registry.registered_methods():
                handler = inspect.unwrap(registry.resolve(method))
                path = Path(inspect.getfile(handler)).resolve()
                entry = {
                    "method": method,
                    "actual_callable": handler.__module__ + "." + handler.__qualname__,
                    "source": path.relative_to(ROOT).as_posix(),
                    "line": inspect.getsourcelines(handler)[1],
                    "callable_sha256": hashlib.sha256(
                        inspect.getsource(handler).encode()
                    ).hexdigest(),
                }
                if call_graph:
                    from atomicity_call_graph import trace

                    entry["call_graph"] = trace(handler)
                result.append(entry)
        finally:
            runtime.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-bindings", action="store_true")
    parser.add_argument("--call-graph", action="store_true")
    parser.add_argument("--configured", action="store_true")
    parser.add_argument("--projectpack-probe", action="store_true")
    options = parser.parse_args()
    inventory = static_inventory()
    inventory["configuration"] = {
        "runtime_bindings": options.runtime_bindings,
        "call_graph": options.call_graph,
        "configured": options.configured,
        "projectpack_probe": options.projectpack_probe,
    }
    if options.runtime_bindings:
        inventory["runtime_bindings"] = registered_bindings(
            call_graph=options.call_graph, configured=options.configured
        )
    if options.projectpack_probe:
        inventory["projectpack_probe"] = projectpack_probe()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "symbols": len(inventory["symbols"]),
                "registrations": len(inventory["registrations"]),
                "unresolved_registrations": len(inventory["unresolved_registrations"]),
                "runtime_bindings": len(inventory.get("runtime_bindings", [])),
            }
        )
    )
    return 0


def projectpack_probe() -> dict[str, Any]:
    """Observe actual constructors in the existing scripted demo, in a disposable test workspace."""
    from atomicity_call_graph import trace

    from thoth.adapters.projectpacks import load_project_pack
    from thoth.apps.projectpack_execution import run_project_pack

    observed: dict[str, Any] = {}
    called: dict[tuple[int, str], Any] = {}
    code = run_project_pack.__code__

    def observe(frame: Any, event: str, arg: Any) -> None:
        if frame.f_code is code and event == "return":
            observed.update(frame.f_locals)
        module = str(frame.f_globals.get("__name__", ""))
        if event == "call" and module.startswith(
            ("thoth.application.", "thoth.apps.", "thoth.adapters.storage.")
        ):
            owner = frame.f_locals.get("self")
            name = frame.f_code.co_name
            if (
                owner is not None
                and name != "__init__"
                and inspect.isfunction(inspect.getattr_static(type(owner), name, None))
            ):
                called[(id(owner), name)] = getattr(owner, name)

    prior = sys.getprofile()
    with TemporaryDirectory(prefix="thoth-atomicity-pack-") as directory:
        pack = load_project_pack(ROOT / "examples/projectpacks/demo-system", include_scripted=True)
        try:
            sys.setprofile(observe)
            result = asyncio.run(run_project_pack(pack, workspace=Path(directory)))
        finally:
            sys.setprofile(prior)
        assert result.scripted_model and "stores" in observed
        graph = trace(run_project_pack, observed)
        graphs = [trace(handler) for handler in called.values()]
        graph["symbols"] = sorted(set(graph["symbols"]).union(*(set(g["symbols"]) for g in graphs)))
        graph["edges"] = sorted(
            set(map(tuple, graph["edges"])).union(*(set(map(tuple, g["edges"])) for g in graphs))
        )
        writer_map: dict[str, set[str]] = {}
        for g in [graph, *graphs]:
            for writer in g["sql_writers"]:
                writer_map.setdefault(writer["symbol"], set()).update(writer["tables"])
        graph["sql_writers"] = [
            {"symbol": s, "tables": sorted(t)} for s, t in sorted(writer_map.items())
        ]
        return {
            "model": "SCRIPTED_LOCAL",
            "evidence_count": result.evidence_count,
            "actual_callable": "thoth.apps.projectpack_execution.run_project_pack",
            "call_graph": graph,
            "observed_callable_count": len(called),
            "scope": "Observed local fixture constructor bindings; not live scientific quality.",
        }


if __name__ == "__main__":
    raise SystemExit(main())
