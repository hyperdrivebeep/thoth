"""Read-only callable traversal of the constructed local runtime; no handler is executed."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from dataclasses import dataclass
from functools import cache
from types import MethodType
from typing import Any

from sqlalchemy import Table

PREFIXES = ("thoth.application.", "thoth.apps.", "thoth.adapters.storage.")
UNRESOLVED = object()


@dataclass
class DeclaredInstance:
    cls: Any
    attributes: dict[str, Any]


CONSTRUCTIONS: dict[tuple[Any, tuple[tuple[str, int], ...]], DeclaredInstance] = {}


def target_name(value: Any) -> str:
    return str(getattr(value, "__module__", "")) + "." + str(getattr(value, "__qualname__", ""))


def resolve(node: ast.AST, env: dict[str, Any], globals_: dict[str, Any]) -> Any:
    if isinstance(node, ast.Name):
        return env.get(node.id, globals_.get(node.id, UNRESOLVED))
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        return {}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    if isinstance(node, ast.Attribute):
        owner = resolve(node.value, env, globals_)
        if owner is UNRESOLVED:
            return UNRESOLVED
        if isinstance(owner, DeclaredInstance):
            if node.attr in owner.attributes:
                return owner.attributes[node.attr]
            static = inspect.getattr_static(owner.cls, node.attr, UNRESOLVED)
            if inspect.isfunction(static):
                return MethodType(static, owner)
            return UNRESOLVED
        try:
            static = inspect.getattr_static(owner, node.attr)
            if isinstance(static, property):
                return UNRESOLVED
            if inspect.isclass(owner) and inspect.isfunction(static):
                return MethodType(static, owner)
            return getattr(owner, node.attr)
        except (AttributeError, TypeError):
            return UNRESOLVED
    if isinstance(node, ast.Call):
        cls = resolve(node.func, env, globals_)
        if inspect.isclass(cls) and str(cls.__module__).startswith(PREFIXES):
            args: dict[str, Any] = {}
            for parameter, value in zip(inspect.signature(cls).parameters, node.args, strict=False):
                args[parameter] = resolve(value, env, globals_)
            for keyword in node.keywords:
                if keyword.arg is not None:
                    args[keyword.arg] = resolve(keyword.value, env, globals_)
            identity = (cls, tuple(sorted((k, id(v)) for k, v in args.items())))
            if identity in CONSTRUCTIONS:
                return CONSTRUCTIONS[identity]
            instance = DeclaredInstance(cls, {})
            CONSTRUCTIONS[identity] = instance
            try:
                initializer = inspect.unwrap(cls.__init__)
                init_tree = source_tree(initializer)
                for assignment in ast.walk(init_tree):
                    if not isinstance(assignment, ast.Assign):
                        continue
                    for target in assignment.targets:
                        targets = target.elts if isinstance(target, ast.Tuple) else [target]
                        values = (
                            assignment.value.elts
                            if isinstance(assignment.value, ast.Tuple)
                            else [assignment.value]
                        )
                        for field, expression in zip(targets, values, strict=False):
                            if (
                                isinstance(field, ast.Attribute)
                                and isinstance(field.value, ast.Name)
                                and field.value.id == "self"
                            ):
                                value = resolve(expression, args, initializer.__globals__)
                                if value is not UNRESOLVED:
                                    instance.attributes[field.attr] = value
            except (TypeError, OSError, AttributeError):
                pass
            return instance
    return UNRESOLVED


def function_context(target: Any, supplied: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    env = dict(supplied)
    if inspect.ismethod(target):
        function = inspect.unwrap(target.__func__)
        receiver = next(iter(inspect.signature(function).parameters))
        env[receiver] = target.__self__
    else:
        function = inspect.unwrap(target)
    return function, env


@cache
def source_tree(function: Any) -> ast.Module:
    return ast.parse(textwrap.dedent(inspect.getsource(function)))


def yielded_type(target: Any, depth: int = 0) -> Any:
    if depth > 3 or not (inspect.ismethod(target) or inspect.isfunction(target)):
        return UNRESOLVED
    function, env = function_context(target, {})
    if not target_name(function).startswith(PREFIXES):
        return UNRESOLVED
    try:
        tree = source_tree(function)
    except (OSError, TypeError, SyntaxError):
        return UNRESOLVED
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            inner = resolve(node.value.func, env, function.__globals__)
            if not inspect.isclass(inner):
                candidate = yielded_type(inner, depth + 1)
                if candidate is not UNRESOLVED:
                    return candidate
        if isinstance(node, ast.Yield) and isinstance(node.value, ast.Call):
            candidate = resolve(node.value.func, env, function.__globals__)
            if inspect.isclass(candidate):
                return candidate
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call) and isinstance(
                    item.optional_vars, ast.Name
                ):
                    inner = resolve(item.context_expr.func, env, function.__globals__)
                    candidate = yielded_type(inner, depth + 1)
                    if candidate is not UNRESOLVED:
                        env[item.optional_vars.id] = candidate
        if isinstance(node, ast.Yield) and isinstance(node.value, ast.Name):
            candidate = env.get(node.value.id, UNRESOLVED)
            if inspect.isclass(candidate):
                return candidate
    return UNRESOLVED


def trace(handler: Any, initial_env: dict[str, Any] | None = None) -> dict[str, Any]:
    queue: list[tuple[Any, dict[str, Any]]] = [(handler, initial_env or {})]
    visited: set[tuple[int, tuple[tuple[str, int], ...]]] = set()
    symbols: set[str] = set()
    writers: dict[str, set[str]] = {}
    edges: set[tuple[str, str]] = set()
    unresolved: set[tuple[str, str]] = set()
    while queue:
        target, supplied = queue.pop()
        function, env = function_context(target, supplied)
        identity = (
            id(function),
            tuple(
                sorted(
                    (k, id(v))
                    for k, v in env.items()
                    if not isinstance(v, (str, int, float, bool, type(None)))
                )
            ),
        )
        if identity in visited or not target_name(function).startswith(PREFIXES):
            continue
        visited.add(identity)
        name = target_name(function)
        symbols.add(name)
        try:
            tree = source_tree(function)
        except (OSError, TypeError, SyntaxError):
            unresolved.add((name, "SOURCE_UNAVAILABLE"))
            continue
        globals_ = function.__globals__
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                actual = resolve(node.value, env, globals_)
                if actual is not UNRESOLVED:
                    for binding in node.targets:
                        if isinstance(binding, ast.Name) and binding.id not in env:
                            env[binding.id] = actual
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
            ):
                actual = resolve(node.value, env, globals_)
                if actual is not UNRESOLVED:
                    env[node.target.id] = actual
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    if isinstance(item.context_expr, ast.Call) and isinstance(
                        item.optional_vars, ast.Name
                    ):
                        alias = yielded_type(resolve(item.context_expr.func, env, globals_))
                        if alias is not UNRESOLVED:
                            env[item.optional_vars.id] = alias
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            expression = ast.unparse(node.func)
            callee = resolve(node.func, env, globals_)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"insert", "update", "delete", "sqlite_insert"}
                and node.args
            ):
                table = resolve(node.args[0], env, globals_)
                writers.setdefault(name, set()).add(
                    table.name
                    if isinstance(table, Table)
                    else "UNRESOLVED_TABLE:" + ast.unparse(node.args[0])
                )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "exec_driver_sql"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                sql = str(node.args[0].value)
                match = re.match(r"\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w]+)", sql, re.I)
                if match:
                    writers.setdefault(name, set()).add(match.group(1))
            if callee is UNRESOLVED:
                if isinstance(node.func, ast.Attribute) and expression.startswith(
                    ("self.", "cls.", "host.", "service.", "transaction.", "tx.")
                ):
                    unresolved.add((name, expression))
                continue
            if not (inspect.ismethod(callee) or inspect.isfunction(callee)) or not target_name(
                callee
            ).startswith(PREFIXES):
                continue
            edges.add((name, target_name(callee)))
            parameters: dict[str, Any] = {}
            signature = inspect.signature(callee)
            positional = [
                p
                for p in signature.parameters.values()
                if p.kind in {p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD}
            ]
            for parameter, argument in zip(positional, node.args, strict=False):
                actual = resolve(argument, env, globals_)
                if actual is not UNRESOLVED:
                    parameters[parameter.name] = actual
            for keyword in node.keywords:
                if keyword.arg is not None:
                    actual = resolve(keyword.value, env, globals_)
                    if actual is not UNRESOLVED:
                        parameters[keyword.arg] = actual
            queue.append((callee, parameters))
    return {
        "symbols": sorted(symbols),
        "edges": sorted(edges),
        "sql_writers": [
            {"symbol": symbol, "tables": sorted(tables)}
            for symbol, tables in sorted(writers.items())
        ],
        "unresolved_calls": sorted(unresolved),
        "note": "Static candidates need phase review; unresolved calls are not assumed safe.",
    }
