"""Classify a small Python subset without evaluating user code."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path


class UnsupportedPython(ValueError):
    pass


@dataclass
class Value:
    kind: str
    literal: object = None
    receiver: Value | None = None


@dataclass(frozen=True)
class PythonEffects:
    classification: str
    read_targets: tuple[str, ...] = ()
    write_targets: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class Analyzer:
    cwd: Path
    values: dict[str, Value] = field(
        default_factory=lambda: {
            name: Value("builtin", name)
            for name in ("print", "len", "str", "list", "tuple", "sorted", "open")
        }
    )
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)

    def path(self, text: str) -> str:
        if not text or len(text) > 4096 or "\x00" in text:
            raise UnsupportedPython("UNSUPPORTED_PATH")
        value = Path(text)
        if text.startswith(("\\\\", "//")) or (value.drive and not value.is_absolute()):
            raise UnsupportedPython("UNSUPPORTED_NETWORK_OR_DRIVE_RELATIVE_PATH")
        return os.path.normpath(str(value if value.is_absolute() else self.cwd / value))

    def imported(self, module: str) -> None:
        if module not in {"pathlib", "hashlib"}:
            raise UnsupportedPython("UNSUPPORTED_IMPORT")
        names = (module, "_hashlib", "_blake2") if module == "hashlib" else (module,)
        if any(
            (self.cwd / (name + ".py")).exists() or (self.cwd / name / "__init__.py").exists()
            for name in names
        ):
            raise UnsupportedPython("LOCAL_STANDARD_MODULE_SHADOW")

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.imported(alias.name)
                self.values[alias.asname or alias.name] = Value("module", alias.name)
            return
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                raise UnsupportedPython("UNSUPPORTED_IMPORT")
            self.imported(node.module)
            for alias in node.names:
                key = (node.module, alias.name)
                kind = {("pathlib", "Path"): "Path", ("hashlib", "sha256"): "sha256"}.get(key)
                if kind is None:
                    raise UnsupportedPython("UNSUPPORTED_IMPORT_MEMBER")
                self.values[alias.asname or alias.name] = Value("callable", kind)
            return
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            self.values[node.targets[0].id] = self.expression(node.value)
            return
        if isinstance(node, ast.Expr):
            self.expression(node.value)
            return
        raise UnsupportedPython("UNSUPPORTED_STATEMENT")

    def expression(self, node: ast.expr, depth: int = 0) -> Value:
        if depth > 32:
            raise UnsupportedPython("EXPRESSION_BUDGET")
        if isinstance(node, ast.Constant) and isinstance(
            node.value, str | bytes | int | bool | type(None)
        ):
            return Value(type(node.value).__name__, node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.values:
                raise UnsupportedPython("UNBOUND_NAME")
            return self.values[node.id]
        if isinstance(node, ast.List | ast.Tuple):
            for item in node.elts:
                self.expression(item, depth + 1)
            return Value("list" if isinstance(node, ast.List) else "tuple")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = (
                self.expression(node.left, depth + 1),
                self.expression(node.right, depth + 1),
            )
            if left.kind != "path" or right.kind != "str" or not isinstance(right.literal, str):
                raise UnsupportedPython("NON_LITERAL_PATH_COMPOSITION")
            return Value("path", self.path(str(Path(str(left.literal)) / right.literal)))
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise UnsupportedPython("PRIVATE_OR_DYNAMIC_ATTRIBUTE")
            owner = self.expression(node.value, depth + 1)
            if owner.kind == "module":
                key = (owner.literal, node.attr)
                name = {("pathlib", "Path"): "Path", ("hashlib", "sha256"): "sha256"}.get(key)
                if name is None:
                    raise UnsupportedPython("UNSUPPORTED_MODULE_MEMBER")
                return Value("callable", name)
            if owner.kind == "path" and node.attr in {"name", "suffix", "parent"}:
                path = Path(str(owner.literal))
                return (
                    Value("path", str(path.parent))
                    if node.attr == "parent"
                    else Value("str", getattr(path, node.attr))
                )
            return Value("method", node.attr, owner)
        if isinstance(node, ast.Call):
            if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                key.arg is None for key in node.keywords
            ):
                raise UnsupportedPython("DYNAMIC_ARGUMENTS")
            target = self.expression(node.func, depth + 1)
            args = [self.expression(arg, depth + 1) for arg in node.args]
            kwargs = {str(key.arg): self.expression(key.value, depth + 1) for key in node.keywords}
            return self.call(target, args, kwargs)
        raise UnsupportedPython("UNSUPPORTED_EXPRESSION")

    def call(self, target: Value, args: list[Value], kwargs: dict[str, Value]) -> Value:
        if target.kind == "callable":
            if (
                target.literal == "Path"
                and len(args) == 1
                and not kwargs
                and args[0].kind == "str"
                and isinstance(args[0].literal, str)
            ):
                return Value("path", self.path(args[0].literal))
            if (
                target.literal == "sha256"
                and len(args) == 1
                and not kwargs
                and args[0].kind == "bytes"
            ):
                return Value("hasher")
            raise UnsupportedPython("UNSUPPORTED_CONSTRUCTOR_ARGUMENTS")
        if target.kind == "builtin":
            if target.literal == "print":
                if set(kwargs) - {"sep", "end", "flush"}:
                    raise UnsupportedPython("PRINT_REDIRECTION")
                if any(
                    value.kind
                    not in {"str", "bytes", "int", "bool", "NoneType", "path", "list", "tuple"}
                    for value in args
                ):
                    raise UnsupportedPython("DYNAMIC_STRING_CONVERSION")
                return Value("NoneType")
            if target.literal == "open":
                return self.open_file(args, kwargs)
            if target.literal in {"list", "tuple"} and not kwargs and len(args) <= 1:
                if args and args[0].kind not in {"list", "tuple", "str", "bytes"}:
                    raise UnsupportedPython("DYNAMIC_ITERATION")
                return Value(str(target.literal))
            if target.literal in {"len", "sorted", "str"} and len(args) == 1 and not kwargs:
                if args[0].kind not in {
                    "str",
                    "bytes",
                    "list",
                    "tuple",
                    "path",
                    "int",
                    "bool",
                    "NoneType",
                }:
                    raise UnsupportedPython("DYNAMIC_BUILTIN_ARGUMENT")
                return Value({"len": "int", "sorted": "list", "str": "str"}[str(target.literal)])
            raise UnsupportedPython("UNSUPPORTED_BUILTIN")
        if target.kind != "method" or target.receiver is None:
            raise UnsupportedPython("DYNAMIC_CALL")
        receiver, name = target.receiver, str(target.literal)
        if receiver.kind == "path":
            return self.path_method(receiver, name, args, kwargs)
        if (
            receiver.kind == "list"
            and name in {"append", "extend"}
            and len(args) == 1
            and not kwargs
        ):
            if name == "extend" and args[0].kind not in {"list", "tuple", "str", "bytes"}:
                raise UnsupportedPython("DYNAMIC_ITERATION")
            return Value("NoneType")
        if (
            receiver.kind == "hasher"
            and name in {"digest", "hexdigest"}
            and not args
            and not kwargs
        ):
            return Value("bytes" if name == "digest" else "str")
        if (
            receiver.kind == "hasher"
            and name == "update"
            and len(args) == 1
            and args[0].kind == "bytes"
            and not kwargs
        ):
            return Value("NoneType")
        if receiver.kind in {"file_read", "file_write"}:
            if name == "close" and not args and not kwargs:
                return Value("NoneType")
            if (
                name == "read"
                and not kwargs
                and (not args or (len(args) == 1 and args[0].kind == "int"))
            ):
                self.reads.add(str(receiver.literal))
                return Value("str")
            if name == "write" and receiver.kind == "file_write" and len(args) == 1 and not kwargs:
                self.writes.add(str(receiver.literal))
                return Value("int")
        raise UnsupportedPython("UNSUPPORTED_METHOD")

    def path_method(
        self, receiver: Value, name: str, args: list[Value], kwargs: dict[str, Value]
    ) -> Value:
        path = str(receiver.literal)
        if name in {"read_bytes", "read_text"} and not args:
            if name == "read_bytes" and kwargs:
                raise UnsupportedPython("UNSUPPORTED_READ_ARGUMENT")
            if set(kwargs) - {"encoding"} or any(
                value.literal not in {"utf-8", "utf-8-sig", "ascii"} for value in kwargs.values()
            ):
                raise UnsupportedPython("UNSUPPORTED_READ_ENCODING")
            self.reads.add(path)
            return Value("bytes" if name == "read_bytes" else "str")
        if (
            name in {"exists", "is_file", "is_dir", "absolute", "as_posix"}
            and not args
            and not kwargs
        ):
            self.reads.add(path)
            return (
                Value("path", path)
                if name == "absolute"
                else Value("str", Path(path).as_posix())
                if name == "as_posix"
                else Value("bool")
            )
        if name in {"write_text", "write_bytes"} and len(args) == 1:
            expected_kind = "str" if name == "write_text" else "bytes"
            if (
                set(kwargs) - {"encoding"}
                or args[0].kind != expected_kind
                or (name == "write_bytes" and kwargs)
            ):
                raise UnsupportedPython("UNSUPPORTED_WRITE_ARGUMENT")
            if any(
                value.literal not in {"utf-8", "utf-8-sig", "ascii"} for value in kwargs.values()
            ):
                raise UnsupportedPython("UNSUPPORTED_WRITE_ARGUMENT")
            self.writes.add(path)
            return Value("int")
        if (
            name in {"touch", "unlink", "mkdir"}
            and not args
            and set(kwargs) <= {"exist_ok", "missing_ok", "parents"}
        ):
            if any(value.kind != "bool" for value in kwargs.values()):
                raise UnsupportedPython("UNSUPPORTED_FILE_OPTION")
            self.writes.add(path)
            return Value("NoneType")
        if (
            name in {"rename", "replace"}
            and len(args) == 1
            and not kwargs
            and args[0].kind in {"str", "path"}
            and isinstance(args[0].literal, str)
        ):
            destination = self.path(args[0].literal)
            self.writes.update((path, destination))
            return Value("path", destination)
        raise UnsupportedPython("UNSUPPORTED_PATH_METHOD")

    def open_file(self, args: list[Value], kwargs: dict[str, Value]) -> Value:
        if not 1 <= len(args) <= 2 or set(kwargs) - {"mode", "encoding"}:
            raise UnsupportedPython("UNSUPPORTED_OPEN")
        if len(args) == 2 and "mode" in kwargs:
            raise UnsupportedPython("DUPLICATE_OPEN_MODE")
        if args[0].kind not in {"str", "path"} or not isinstance(args[0].literal, str):
            raise UnsupportedPython("DYNAMIC_OPEN_PATH")
        mode = args[1] if len(args) == 2 else kwargs.get("mode", Value("str", "r"))
        if mode.kind != "str" or mode.literal not in {
            "r",
            "rb",
            "w",
            "wb",
            "a",
            "ab",
            "x",
            "xb",
            "r+",
            "w+",
            "a+",
        }:
            raise UnsupportedPython("UNSUPPORTED_OPEN_MODE")
        if "encoding" in kwargs and kwargs["encoding"].literal not in {
            "utf-8",
            "utf-8-sig",
            "ascii",
        }:
            raise UnsupportedPython("UNSUPPORTED_OPEN_ENCODING")
        path = self.path(args[0].literal)
        writing = any(letter in str(mode.literal) for letter in "wax+")
        (self.writes if writing else self.reads).add(path)
        return Value("file_write" if writing else "file_read", path)


def analyze_python(source: str, *, cwd: Path) -> PythonEffects:
    if len(source) > 65536:
        return PythonEffects("UNCLASSIFIED", reason="PYTHON_SOURCE_BUDGET")
    analyzer = Analyzer(cwd)
    try:
        tree = ast.parse(source)
        if sum(1 for _ in ast.walk(tree)) > 2048:
            raise UnsupportedPython("PYTHON_AST_BUDGET")
        for statement in tree.body:
            analyzer.statement(statement)
        return PythonEffects(
            "WRITE" if analyzer.writes else "READ_ONLY",
            tuple(sorted(analyzer.reads)),
            tuple(sorted(analyzer.writes)),
        )
    except (SyntaxError, UnsupportedPython, RecursionError, OSError, ValueError):
        return PythonEffects("UNCLASSIFIED", reason="UNSUPPORTED_PYTHON_SUBSET")
