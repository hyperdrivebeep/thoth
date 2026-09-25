from __future__ import annotations

import ast
import json
from pathlib import Path

try:
    from scripts.architecture_contract import ROOT
    from scripts.extension_binding_contract import SourceIndex, binding_errors
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from architecture_contract import ROOT
    from extension_binding_contract import SourceIndex, binding_errors

_CONNECTOR_KIND_LITERALS = {
    "LOCAL",
    "GIT",
    "POSTGRES",
    "S3",
    "MCP",
    "REST",
    "CUSTOM",
}
_CONNECTOR_SELECTOR_LITERALS = {
    "relative_path",
    "repository_path",
    "revision",
    "file_path",
    "bucket",
    "key",
    "view",
    "uri",
}
_SANDBOX_CLASS_LITERALS = {
    "DockerSandboxAdapter",
    "GVisorSandboxAdapter",
    "FirecrackerSandboxAdapter",
    "E2BManagedSandboxAdapter",
    "ScriptedSandboxAdapter",
}
_CONCRETE_EXTENSION_LITERALS = _SANDBOX_CLASS_LITERALS | {
    "managed-e2b",
    "docker-poc",
    "c08-postgres-readonly",
    "codex-oauth/account-default",
}
_REQUIRED_REGISTRIES = {"MODEL", "PARSER", "CONNECTOR", "SANDBOX", "EVALUATOR"}


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
        ),
        None,
    )


def _string_literals(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def connector_route_errors(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    route = _function(tree, "route")
    if route is None:
        return ["OCP-CONNECTOR-001: ConnectorService.route is missing"]
    literals = _string_literals(route)
    forbidden = sorted(
        literals.intersection(_CONNECTOR_KIND_LITERALS | _CONNECTOR_SELECTOR_LITERALS)
    )
    source_kind_reads = [
        node
        for node in ast.walk(route)
        if isinstance(node, ast.Attribute) and node.attr == "source_kind"
    ]
    errors: list[str] = []
    if forbidden:
        errors.append(
            f"OCP-CONNECTOR-001: application route enumerates connector contracts: {forbidden}"
        )
    if source_kind_reads:
        errors.append("OCP-CONNECTOR-001: application route reads connector source_kind")
    return errors


def sandbox_runtime_errors(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    create_runtime = _function(tree, "create_runtime")
    if create_runtime is None:
        return ["OCP-SANDBOX-001: create_runtime is missing"]
    literals = _string_literals(create_runtime)
    forbidden = sorted(literals.intersection(_SANDBOX_CLASS_LITERALS))
    class_name_reads = [
        node
        for node in ast.walk(create_runtime)
        if isinstance(node, ast.Attribute) and node.attr == "__name__"
    ]
    errors: list[str] = []
    if forbidden:
        errors.append(f"OCP-SANDBOX-001: runtime enumerates concrete sandbox classes: {forbidden}")
    if class_name_reads:
        errors.append("OCP-SANDBOX-001: runtime derives routing from a class __name__")
    return errors


def hero_trace_errors(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    protected = {"normal_entry_method", "manual_semantic_rpc_assembly"}
    literal_claims: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value in protected
                and isinstance(value, ast.Constant)
            ):
                literal_claims.append(str(key.value))
    return (
        [
            "OCP-HERO-001: Hero execution claims are self-authored literals: "
            f"{sorted(literal_claims)}"
        ]
        if literal_claims
        else []
    )


def _defined_symbols(root: Path) -> set[str]:
    symbols: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                symbols.add(node.name)
    return symbols


def extension_manifest_errors(root: Path) -> list[str]:
    manifest = json.loads(
        (root / "config/architecture-conformance.json").read_text(encoding="utf-8")
    )
    index = SourceIndex(root)
    extensions = {str(item["name"]): item for item in manifest["extension_points"]}
    errors: list[str] = []
    missing = sorted(_REQUIRED_REGISTRIES - extensions.keys())
    if missing:
        errors.append(f"OCP-REGISTRY-001: required extension registries are missing: {missing}")
    for name, item in extensions.items():
        status = str(item.get("status", ""))
        if status == "IMPLEMENTED":
            errors.extend(binding_errors(index, name, item))
        if status == "PARTIAL":
            acceptance_ids = item.get("acceptance_ids")
            if (
                not item.get("debt_id")
                or not isinstance(acceptance_ids, list)
                or not acceptance_ids
            ):
                errors.append(
                    f"OCP-REGISTRY-001: {name} PARTIAL state lacks explicit debt ownership"
                )
    return errors


def core_closed_errors(application_root: Path) -> list[str]:
    errors: list[str] = []
    for path in application_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare | ast.Match):
                selector = node.left if isinstance(node, ast.Compare) else node.subject
                names = {part.id for part in ast.walk(selector) if isinstance(part, ast.Name)}
                names.update(
                    part.attr for part in ast.walk(selector) if isinstance(part, ast.Attribute)
                )
                if names & {
                    "provider",
                    "provider_id",
                    "adapter",
                    "adapter_id",
                    "evaluator_id",
                } and _string_literals(node):
                    errors.append(
                        f"OCP-CORE-CLOSED-001: concrete identity selection: {path}:{node.lineno}"
                    )
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("thoth.adapters") for alias in node.names
            ):
                errors.append(
                    f"OCP-CORE-CLOSED-001: application imports an adapter: {path}:{node.lineno}"
                )
            if isinstance(node, ast.ImportFrom) and str(node.module).startswith("thoth.adapters"):
                errors.append(
                    "OCP-CORE-CLOSED-001: application imports an adapter: "
                    f"{path.as_posix()}:{node.lineno}"
                )
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _CONCRETE_EXTENSION_LITERALS
            ):
                errors.append(
                    "OCP-CORE-CLOSED-001: application names a concrete extension: "
                    f"{path.as_posix()}:{node.lineno}:{node.value}"
                )
    return errors


def main() -> int:
    errors = [
        *connector_route_errors(ROOT / "src/thoth/application/services/connector_service.py"),
        *sandbox_runtime_errors(ROOT / "src/thoth/apps/runtime.py"),
        *hero_trace_errors(ROOT / "qa/scenarios/hero_6g_current_core.py"),
        *extension_manifest_errors(ROOT),
        *core_closed_errors(ROOT / "src/thoth/application"),
    ]
    print(
        json.dumps(
            {
                "verdict": "PASS" if not errors else "FAIL",
                "rules": [
                    "OCP-CONNECTOR-001",
                    "OCP-SANDBOX-001",
                    "OCP-HERO-001",
                    "OCP-REGISTRY-001",
                    "OCP-FACTORY-001",
                    "OCP-CORE-CLOSED-001",
                ],
                "errors": errors,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
