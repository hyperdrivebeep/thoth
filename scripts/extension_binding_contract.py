"""Bounded source binding checks, supplemented by typechecking and runtime extension tests.

This resolves exact modules (including reexports), protocol methods and reachable composition
sites. It is intentionally not a proof of arbitrary Python dataflow or general OCP.
"""

from __future__ import annotations

import ast
from contextlib import suppress
from pathlib import Path


class SourceIndex:
    def __init__(self, root: Path) -> None:
        self.trees: dict[str, ast.Module] = {}
        for path in (root / "src").rglob("*.py"):
            parts = list(path.relative_to(root / "src").with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            self.trees[".".join(parts)] = ast.parse(path.read_text(encoding="utf-8"))

    def name(self, module: str, node: ast.AST) -> str:
        if isinstance(node, ast.Attribute):
            return self.name(module, node.value) + "." + node.attr
        if not isinstance(node, ast.Name):
            return ast.unparse(node)
        tree = self.trees[module]
        for item in tree.body:
            if isinstance(item, ast.ImportFrom) and item.module:
                for alias in item.names:
                    if (alias.asname or alias.name) == node.id:
                        return f"{item.module}.{alias.name}"
            if isinstance(item, ast.Import):
                for alias in item.names:
                    if (alias.asname or alias.name) == node.id:
                        return alias.name
        return f"{module}.{node.id}"

    def resolve(self, reference: str, seen: frozenset[str] = frozenset()) -> tuple[str, ast.AST]:
        if reference in seen:
            raise ValueError(f"cyclic source export: {reference}")
        module, _, symbol = reference.rpartition(".")
        tree = self.trees.get(module)
        if tree is None:
            owner_module, _, owner = module.rpartition(".")
            if owner_module in self.trees and any(
                isinstance(node, ast.ClassDef) and node.name == owner
                for node in self.trees[owner_module].body
            ):
                raise ValueError(
                    f"method-qualified consumer is unsupported: {reference}; "
                    f"use its top-level class {module} (method bodies are inspected)"
                )
            raise ValueError(f"exact source module is missing: {reference}")
        for node in tree.body:
            if (
                isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == symbol
            ):
                return module, node
        exported = self.name(module, ast.Name(id=symbol))
        if exported != reference:
            return self.resolve(exported, seen | {reference})
        raise ValueError(f"exact source symbol is missing: {reference}")

    def members(self, module: str, node: ast.AST) -> dict[str, tuple[str, ast.AST]]:
        if not isinstance(node, ast.ClassDef):
            raise ValueError("extension target must be a concrete class")
        result: dict[str, tuple[str, ast.AST]] = {}
        for base in node.bases:
            with suppress(ValueError):
                result.update(self.members(*self.resolve(self.name(module, base))))
        for member in node.body:
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                result[member.name] = (module, member)
        return result

    def signature(self, module: str, method: ast.AST) -> tuple[object, ...]:
        if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            return ()

        # Imported domain types use qualified names; builtin/generic spelling is stable here.
        def annotation(value: ast.AST | None) -> str:
            if value is None:
                return "MISSING"
            text = ast.unparse(value)
            for name in sorted(
                {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}, key=len, reverse=True
            ):
                qualified = self.name(module, ast.Name(id=name))
                local_type = any(
                    isinstance(item, ast.ClassDef) and item.name == name
                    for item in self.trees[module].body
                )
                if qualified.startswith(("thoth.", "pydantic.")) and (
                    qualified != module + "." + name or local_type
                ):
                    text = text.replace(name, qualified)
            return text

        positional = method.args.posonlyargs + method.args.args
        return (
            isinstance(method, ast.AsyncFunctionDef),
            tuple((arg.arg, annotation(arg.annotation)) for arg in positional if arg.arg != "self"),
            tuple((arg.arg, annotation(arg.annotation)) for arg in method.args.kwonlyargs),
            annotation(method.returns),
        )

    def composition(self, target: str, entry: str, registration: str | None) -> None:
        module, node = self.resolve(entry)
        target_module, target_node = self.resolve(target)
        calls = []
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            try:
                resolved = self.resolve(self.name(module, call.func))
            except ValueError:
                continue
            if resolved == (target_module, target_node):
                calls.append(call)
        if not calls:
            raise ValueError(f"composition {entry} does not construct {target}")
        if registration:
            initializer = self.members(target_module, target_node).get("__init__")
            if initializer is not None and any(call.args or call.keywords for call in calls):
                if any(
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"
                    and call.func.attr == registration
                    for call in ast.walk(initializer[1])
                ):
                    return
            receivers = {
                assignment.targets[0].id
                for assignment in ast.walk(node)
                if isinstance(assignment, ast.Assign)
                and assignment.value in calls
                and isinstance(assignment.targets[0], ast.Name)
            }
            if not any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in receivers
                and call.func.attr == registration
                for call in ast.walk(node)
            ):
                raise ValueError(f"composition {entry} is missing {target} registration")


def binding_errors(index: SourceIndex, name: str, item: dict) -> list[str]:
    try:
        target = item["factory_target"]
        module, node = index.resolve(target)
        port_module, port = index.resolve(item["factory_port"])
        if not isinstance(node, ast.ClassDef) or any(
            index.name(module, base) == "typing.Protocol" for base in node.bases
        ):
            raise ValueError(
                "factory target is an abstract protocol, not a registration/composition factory"
            )
        required = index.members(port_module, port)
        members = index.members(module, node)
        for method, (owner, contract) in required.items():
            if method.startswith("_"):
                continue
            if method not in members or index.signature(*members[method]) != index.signature(
                owner, contract
            ):
                raise ValueError(f"factory does not implement protocol method {method}")
            implementation = members[method][1]
            if isinstance(implementation, ast.FunctionDef | ast.AsyncFunctionDef) and all(
                isinstance(statement, ast.Pass)
                or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                    and (statement.value.value is Ellipsis or isinstance(statement.value.value, str)))
                for statement in implementation.body
            ):
                raise ValueError(f"factory inherits an unimplemented protocol method {method}")
        registration = item.get("registration_method")
        if registration and registration not in members:
            raise ValueError("factory registration method is missing")
        index.composition(target, item["composition_target"], registration)
        for consumer in item.get("consumer_targets", []):
            index.composition(item["composition_target"], consumer, None)
        return []
    except (KeyError, ValueError, TypeError) as exc:
        return [f"OCP-FACTORY-001: {name}: {exc}"]
