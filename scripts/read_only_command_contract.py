"""Compose effects of a deliberately narrow, literal shell grammar."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.python_read_contract import analyze_python

HELPERS = "config/read-only-command-helpers.json"
PARSER = "scripts/parse_shell_units.ps1"


@dataclass(frozen=True)
class CommandEffects:
    classification: str
    read_targets: tuple[str, ...] = ()
    write_targets: tuple[str, ...] = ()
    reason: str = ""


class UnsupportedCommand(ValueError):
    pass


def is_intrinsic_read(source: str, *, cwd: Path) -> bool:
    """Small parser-independent read grammar keeps stale helper pins reviewable."""
    if any(char in source for char in ";&|<>$`\n\r()"):
        return False
    try:
        argv = shlex.split(source)
        if not argv or argv[0].lower() not in {"get-content", "get-filehash"}:
            return False
        return _powershell_file(argv, cwd).classification == "READ_ONLY"
    except (ValueError, OSError):
        return False


def is_preflight_bootstrap(source: str, *, root: Path) -> bool:
    """Permit only the gate's own literal CLI, including when parser pins need renewal."""
    if any(char in source for char in ";&|<>$`\n\r()"):
        return False
    try:
        argv = shlex.split(source)
    except ValueError:
        return False
    if len(argv) < 6:
        return False
    executable = argv[0].replace("\\", "/").lower()
    if executable not in {
        ".venv/scripts/python.exe",
        "./.venv/scripts/python.exe",
        (root / ".venv/Scripts/python.exe").as_posix().lower(),
    }:
        return False
    script = argv[1].replace("\\", "/")
    if script not in {
        "scripts/prepare_architecture_preflight.py",
        (root / "scripts/prepare_architecture_preflight.py").as_posix(),
    }:
        return False
    arguments = argv[2:]
    if len(arguments) % 2:
        return False
    pairs = list(zip(arguments[::2], arguments[1::2], strict=True))
    return (
        sum(key == "--acceptance" for key, _ in pairs) == 1
        and any(key == "--scope" for key, _ in pairs)
        and all(
            key in {"--acceptance", "--scope", "--remediates", "--note", "--plan-id", "--node-id", "--host-owner-receipt"}
            and bool(value)
            for key, value in pairs
        )
        and all(
            re.fullmatch(r"A(?:0[1-9]|1[0-3])", value)
            for key, value in pairs
            if key == "--acceptance"
        )
    )


def _path(text: str, cwd: Path) -> str:
    candidate = Path(text)
    if (
        not text
        or "\0" in text
        or text.startswith(("//", "\\\\"))
        or (candidate.drive and not candidate.is_absolute())
        or any(char in text for char in "*?[]")
    ):
        raise UnsupportedCommand("NON_LITERAL_FILESYSTEM_TARGET")
    return os.path.normpath(str(candidate if candidate.is_absolute() else cwd / candidate))


def is_rule_recovery_workflow(source: str, *, root: Path) -> bool:
    """Literal trusted recovery CLI; the program still verifies archive, scope and drift."""
    if any(char in source for char in ";&|<>$`\n\r()"):
        return False
    try:
        argv = shlex.split(source)
    except ValueError:
        return False
    if len(argv) not in {4, 5, 6, 7}:
        return False
    if argv[0].replace("\\", "/").lower() not in {
        ".venv/scripts/python.exe", "./.venv/scripts/python.exe",
        (root / ".venv/Scripts/python.exe").as_posix().lower(),
    }:
        return False
    script = argv[1].replace("\\", "/")
    if script in {
        "scripts/change_architecture_rules.py",
        (root / "scripts/change_architecture_rules.py").as_posix(),
    }:
        if len(argv) == 5 and argv[2:4] == ["stage", "--candidate"]:
            return bool(argv[4])
        return bool(
            len(argv) == 7 and argv[2] in {"apply", "resume", "rollback"}
            and argv[3] == "--proposal" and argv[5] == "--approve-proposal"
            and re.fullmatch(r"[0-9a-f]{64}", argv[4]) and argv[4] == argv[6]
        )
    if script in {
        "scripts/cancel_architecture_preflight.py",
        (root / "scripts/cancel_architecture_preflight.py").as_posix(),
    }:
        return len(argv) == 4 and argv[2] == "--reason" and bool(argv[3].strip())
    if script not in {
        "scripts/restore_architecture_rules.py",
        (root / "scripts/restore_architecture_rules.py").as_posix(),
    }:
        return False
    return bool(
        argv[2] == "--preflight" and re.fullmatch(r"[0-9a-f]{64}", argv[3])
        and (len(argv) == 4 or (
            argv[4] == "--expected-drift" and re.fullmatch(r"[0-9a-f]{64}", argv[5])
        ))
    )


def _pinned(root: Path, paths: dict[str, str]) -> None:
    for name, expected in paths.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise UnsupportedCommand("HELPER_PATH_INVALID")
        path = root / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise UnsupportedCommand("HELPER_HASH_DIFFERS")


def parse_units(source: str, root: Path) -> list[dict[str, Any]]:
    manifest = json.loads((root / HELPERS).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0.0":
        raise UnsupportedCommand("HELPER_MANIFEST_INVALID")
    _pinned(root, {PARSER: manifest["parser_sha256"]})
    # Only this fixed, hash-checked program is executable; source travels on stdin as data.
    program = (root / PARSER).read_text(encoding="utf-8")
    parsed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", program],
        input=source,
        encoding="utf-8",
        capture_output=True,
        cwd=root,
        check=False,
        timeout=8,
    )
    if parsed.returncode:
        raise UnsupportedCommand("SHELL_PARSER_FAILED")
    value = json.loads(parsed.stdout.lstrip("\ufeff"))
    if value.get("schema_version") != "1.0.0" or value.get("unknown"):
        raise UnsupportedCommand("UNSUPPORTED_SHELL_SYNTAX")
    units = value.get("units")
    if not isinstance(units, list) or not units:
        raise UnsupportedCommand("NO_LITERAL_COMMANDS")
    return units


def _arguments(
    values: list[str],
    *,
    value_options: set[str],
    switches: set[str],
) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    options: dict[str, str] = {}
    offset = 0
    while offset < len(values):
        value = values[offset]
        key = value.lower()
        if key == "--":
            positional.extend(values[offset + 1 :])
            break
        if key in switches:
            offset += 1
            continue
        if key in value_options:
            if key in options or offset + 1 >= len(values):
                raise UnsupportedCommand("OPTION_ARGUMENT_INVALID")
            options[key] = values[offset + 1]
            offset += 2
            continue
        if value.startswith("-"):
            raise UnsupportedCommand("UNSUPPORTED_OPTION")
        positional.append(value)
        offset += 1
    return positional, options


def _powershell_file(argv: list[str], cwd: Path) -> CommandEffects:
    name = argv[0].lower()
    reading = name in {"get-content", "get-filehash"}
    values = {"-literalpath", "-path", "-encoding"}
    switches: set[str] = set()
    if name == "get-content":
        values |= {"-totalcount", "-tail"}
        switches |= {"-raw"}
    elif name == "get-filehash":
        values |= {"-algorithm"}
    else:
        values |= {"-value"}
        switches |= {"-nonewline", "-force"}
    positional, options = _arguments(argv[1:], value_options=values, switches=switches)
    if "-path" in options and "-literalpath" in options:
        raise UnsupportedCommand("DUPLICATE_PATH")
    target = options.get("-literalpath", options.get("-path"))
    if target is None and positional:
        target = positional.pop(0)
    if not target or (reading and positional) or len(positional) > 1:
        raise UnsupportedCommand("FILE_ARGUMENTS_INVALID")
    if "-value" in options and positional:
        raise UnsupportedCommand("DUPLICATE_VALUE")
    path = _path(target, cwd)
    return CommandEffects("READ_ONLY", (path,)) if reading else CommandEffects("WRITE", (), (path,))


def _ripgrep(argv: list[str]) -> CommandEffects:
    positional, options = _arguments(
        argv[1:],
        value_options={
            "-g",
            "--glob",
            "-t",
            "--type",
            "-e",
            "--regexp",
            "-f",
            "--file",
            "-a",
            "-b",
            "-c",
            "--max-count",
            "--max-depth",
            "--encoding",
            "--color",
        },
        switches={
            "-n",
            "--line-number",
            "-i",
            "--ignore-case",
            "-s",
            "--smart-case",
            "-f",
            "--fixed-strings",
            "-l",
            "--files-with-matches",
            "--files",
            "--hidden",
            "--no-ignore",
            "--no-heading",
            "--count",
            "--stats",
            "--json",
            "--multiline",
            "-u",
            "--pcre2",
            "-p",
            "--only-matching",
            "-o",
            "--quiet",
            "-q",
            "--no-messages",
        },
    )
    # Every supported argument is data. In particular --pre and --hostname-bin are unsupported.
    return CommandEffects("READ_ONLY", tuple(positional + list(options.values())))


def _git(argv: list[str]) -> CommandEffects:
    if len(argv) < 2:
        raise UnsupportedCommand("GIT_SUBCOMMAND_MISSING")
    subcommand = argv[1]
    if subcommand not in {
        "status",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "rev-list",
        "check-ignore",
        "diff",
        "show",
        "log",
    }:
        raise UnsupportedCommand("UNSUPPORTED_GIT_COMMAND")
    if any(
        arg in {"--ext-diff", "--textconv", "--no-index"}
        or arg.startswith(("--output", "--exec-path", "--config-env"))
        for arg in argv[2:]
    ):
        raise UnsupportedCommand("GIT_EFFECTFUL_OPTION")
    if subcommand in {"diff", "show", "log"} and not {"--no-ext-diff", "--no-textconv"} <= set(
        argv[2:]
    ):
        raise UnsupportedCommand("GIT_EXTERNAL_CONVERSION_NOT_DISABLED")
    return CommandEffects("READ_ONLY")


def _python(argv: list[str], root: Path, cwd: Path) -> CommandEffects:
    if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
        # Literal test entry; explicitly account for CLI-created output paths.
        outputs: list[str] = []
        args = argv[3:]
        for offset, value in enumerate(args):
            if value in {"--basetemp", "--junitxml", "--junit-xml", "--log-file"}:
                if offset + 1 >= len(args):
                    raise UnsupportedCommand("PYTEST_OUTPUT_PATH_MISSING")
                outputs.append(_path(args[offset + 1], cwd))
            for option in ("--basetemp=", "--junitxml=", "--junit-xml=", "--log-file="):
                if value.startswith(option):
                    outputs.append(_path(value[len(option) :], cwd))
        return CommandEffects("WRITE" if outputs else "WORKFLOW", (), tuple(outputs))
    if len(argv) >= 4 and argv[1:3] == ["-m", "ruff"]:
        mutating = argv[3] == "format" or any(arg in {"--fix", "--fix-only"} for arg in argv[4:])
        if argv[3] not in {"check", "format"}:
            raise UnsupportedCommand("UNSUPPORTED_RUFF_ENTRY")
        targets, _ = _arguments(
            argv[4:],
            value_options={"--config", "--select", "--ignore"},
            switches={"--fix", "--fix-only", "--check", "--diff"},
        )
        if not targets:
            raise UnsupportedCommand("RUFF_TARGET_REQUIRED")
        writes = tuple(_path(target, cwd) for target in targets) if mutating else ()
        return CommandEffects("WRITE" if writes else "WORKFLOW", (), writes)
    if len(argv) == 3 and argv[1] == "-c":
        effects = analyze_python(argv[2], cwd=cwd)
        return CommandEffects(
            effects.classification, effects.read_targets, effects.write_targets, effects.reason
        )
    if len(argv) < 2 or argv[1].startswith("-"):
        raise UnsupportedCommand("UNSUPPORTED_PYTHON_ENTRY")
    path = Path(_path(argv[1], cwd))
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise UnsupportedCommand("PYTHON_ENTRY_OUTSIDE_REPOSITORY") from exc
    if relative in {
        "scripts/prepare_architecture_preflight.py",
        "scripts/complete_architecture_gate.py",
        "scripts/record_wiki_sync.py",
        "scripts/cancel_architecture_preflight.py",
    }:
        # These trusted workflow CLIs enforce their own receipt/state contracts. Not a read claim.
        return CommandEffects("WORKFLOW")
    if (
        relative == "scripts/verification_status.py"
        and argv[2:]
        and argv[2] in {"current", "import-historical"}
    ):
        return CommandEffects("WORKFLOW")
    manifest = json.loads((root / HELPERS).read_text(encoding="utf-8"))
    helper = manifest["helpers"].get(relative)
    if relative == "scripts/verification_status.py" and len(argv) > 2 and argv[2] == "running":
        return CommandEffects("READ_ONLY")
    if helper is None or argv[2:] not in helper["allowed_argv"]:
        raise UnsupportedCommand("UNREGISTERED_READ_ONLY_HELPER")
    _pinned(root, helper["files"])
    if relative not in helper["files"]:
        raise UnsupportedCommand("HELPER_ENTRY_NOT_PINNED")
    return CommandEffects("READ_ONLY")


def _unit(unit: dict[str, Any], root: Path, cwd: Path) -> CommandEffects:
    argv = unit.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise UnsupportedCommand("COMMAND_ARGUMENTS_INVALID")
    name = argv[0].replace("\\", "/").lower()
    if name in {"get-content", "get-filehash", "set-content", "add-content", "out-file"}:
        effects = _powershell_file(argv, cwd)
    elif name in {"rg", "rg.exe"}:
        effects = _ripgrep(argv)
    elif name in {"git", "git.exe"}:
        effects = _git(argv)
    elif name in {"ruff", "ruff.exe"}:
        effects = _python(["python", "-m", "ruff", *argv[1:]], root, cwd)
    elif (
        name
        in {
            "python",
            "python.exe",
            "python3",
            "python3.exe",
            ".venv/scripts/python.exe",
            "./.venv/scripts/python.exe",
        }
        or Path(argv[0]) == root / ".venv/Scripts/python.exe"
    ):
        effects = _python(argv, root, cwd)
    elif name in {
        "write-output",
        "echo",
        "select-object",
        "format-table",
        "format-list",
        "measure-object",
    }:
        effects = CommandEffects("READ_ONLY")
    elif name in {"./makefile.ps1", "makefile.ps1"} and argv[1:] in [
        ["architecture"],
        ["verify"],
        ["tooling"],
        ["test"],
        ["lint"],
        ["type"],
        ["doctor"],
        ["web"],
    ]:
        effects = CommandEffects("WORKFLOW")
    else:
        raise UnsupportedCommand("UNSUPPORTED_COMMAND")
    redirects = unit.get("redirects")
    if not isinstance(redirects, list):
        raise UnsupportedCommand("REDIRECTIONS_INVALID")
    writes = set(effects.write_targets)
    for redirect in redirects:
        writes.add(_path(redirect["path"], cwd))
    return CommandEffects(
        "UNCLASSIFIED"
        if effects.classification == "UNCLASSIFIED"
        else "WRITE"
        if writes
        else effects.classification,
        effects.read_targets,
        tuple(sorted(writes)),
        effects.reason,
    )


def analyze_command(
    source: str, *, root: Path, cwd: Path, cwd_known: bool = True
) -> CommandEffects:
    if len(source) > 65536:
        return CommandEffects("UNCLASSIFIED", reason="SOURCE_BUDGET")
    try:
        effects: list[CommandEffects] = []
        for offset, unit in enumerate(parse_units(source, root)):
            argv = unit.get("argv", [])
            if argv and argv[0].lower() == "set-location":
                if (
                    offset != 0
                    or "|" in source
                    or len(argv) != 3
                    or argv[1].lower() != "-literalpath"
                ):
                    raise UnsupportedCommand("UNSUPPORTED_LOCATION_CHANGE")
                destination = Path(argv[2])
                if not destination.is_absolute():
                    raise UnsupportedCommand("ABSOLUTE_LOCATION_REQUIRED")
                cwd = Path(_path(argv[2], cwd))
                cwd_known = True
                effects.append(CommandEffects("WORKFLOW"))
                continue
            effect = _unit(unit, root, cwd)
            if effect.write_targets and not cwd_known:
                return CommandEffects(
                    "UNCLASSIFIED",
                    write_targets=effect.write_targets,
                    reason="TOOL_WORKDIR_UNAVAILABLE_USE_EXPLICIT_LOCATION",
                )
            effects.append(effect)
        if any(effect.classification == "UNCLASSIFIED" for effect in effects):
            raise UnsupportedCommand("UNSUPPORTED_COMMAND_UNIT")
        reads = {target for effect in effects for target in effect.read_targets}
        writes = {target for effect in effects for target in effect.write_targets}
        classification = (
            "WRITE"
            if writes
            else "WORKFLOW"
            if any(effect.classification == "WORKFLOW" for effect in effects)
            else "READ_ONLY"
        )
        return CommandEffects(classification, tuple(sorted(reads)), tuple(sorted(writes)))
    except UnsupportedCommand as exc:
        return CommandEffects("UNCLASSIFIED", reason=str(exc))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return CommandEffects("UNCLASSIFIED", reason="UNCLASSIFIED_REQUIRES_REVIEW")
