from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
    preflight_archive_payload,
)
from scripts.python_read_contract import analyze_python
from scripts.read_only_command_contract import analyze_command
from tests.architecture.owner_helpers import bind_owner

ROOT = Path(__file__).resolve().parents[2]
HASH_AND_SEARCH = (
    '.venv/Scripts/python.exe -c "from pathlib import Path; import hashlib; '
    "print(hashlib.sha256(Path('src/thoth/apps/runtime.py').read_bytes()).hexdigest())\"; "
    'rg -n "class AppRuntime" src/thoth/apps/runtime.py'
)


def hook(command: str, gate: Path, *, cwd: Path = ROOT) -> dict[str, Any] | None:
    result = subprocess.run(
        [sys.executable, str(ROOT / ".codex/hooks/pre_tool_policy.py")],
        input=json.dumps(
            {
                "tool_name": "exec_command",
                "cwd": str(ROOT),
                "session_id": "session-a",
                "turn_id": "turn-fixture",
                "tool_input": {"cmd": command, "workdir": str(cwd)},
            }
        ),
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(gate)},
    )
    assert result.returncode == 0, result.stderr
    return None if not result.stdout.strip() else json.loads(result.stdout)


@pytest.mark.parametrize(
    "command",
    [
        HASH_AND_SEARCH,
        '.venv/Scripts/python.exe -c "items=[]; '
        "items.append('src/thoth/apps/runtime.py'); print(items)\"",
        'rg -n "git commit" src/thoth/apps/runtime.py',
        "Get-Content -LiteralPath src/thoth/apps/runtime.py -TotalCount 2; "
        "Get-FileHash src/thoth/apps/runtime.py",
    ],
)
def test_supported_reads_need_no_edit_preflight(tmp_path: Path, command: str) -> None:
    assert hook(command, tmp_path / "missing.json") is None


@pytest.mark.parametrize(
    "command",
    [
        HASH_AND_SEARCH + "; Set-Content -LiteralPath src/thoth/domain/base.py -Value changed",
        '.venv/Scripts/python.exe -c "from pathlib import Path; '
        "Path('src/thoth/domain/base.py').write_text('x')\"",
        "Get-Content src/thoth/apps/runtime.py > src/thoth/domain/base.py",
        "rg --pre arbitrary-executable pattern src/thoth/apps/runtime.py",
    ],
)
def test_reads_do_not_authorize_adjacent_or_embedded_writes(tmp_path: Path, command: str) -> None:
    output = hook(command, tmp_path / "missing.json")
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def scoped_gate(directory: Path, scope: str) -> Path:
    value = preflight_archive_payload(
        json.loads((ROOT / ".thoth/architecture/preflight.json").read_text(encoding="utf-8"))
    )
    value["declared_scope"] = [scope]
    value["preflight_receipt_id"] = calculate_preflight_receipt_id(value)
    archive_receipt(
        directory, receipt_id=str(value["preflight_receipt_id"]), kind="preflight", payload=value
    )
    path = directory / "preflight.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    bind_owner(directory)
    return path


def test_read_paths_do_not_become_write_scope(tmp_path: Path) -> None:
    scope = "tests/architecture/test_read_only_command_contract.py"
    gate_path = scoped_gate(tmp_path, scope)
    command = (
        '.venv/Scripts/python.exe -c "from pathlib import Path; '
        "data=Path('src/thoth/apps/runtime.py').read_text(encoding='utf-8'); "
        f"Path('{scope}').write_text(data, encoding='utf-8')\""
    )
    # Inspect the ordinary Hook event only; the candidate write is never executed.
    assert hook(command, gate_path) is None
    changed_target = command.replace(scope, "src/thoth/domain/base.py")
    output = hook(changed_target, gate_path)
    assert (
        output is not None and "outside" in output["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_relative_write_is_resolved_against_tool_workdir(tmp_path: Path) -> None:
    gate_path = scoped_gate(tmp_path, "tests/architecture/test_read_only_command_contract.py")
    output = hook(
        "Set-Content -LiteralPath base.py -Value x", gate_path, cwd=ROOT / "src/thoth/domain"
    )
    assert output is not None
    assert "src/thoth/domain/base.py" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_literal_payload_paths_are_not_additional_write_targets(tmp_path: Path) -> None:
    gate_path = scoped_gate(tmp_path, "docs/report.md")
    command = (
        "Set-Content -LiteralPath docs/report.md -Value "
        "'source src/thoth/apps/guard_probe.py and apps/outside.py'"
    )
    assert hook(command, gate_path) is None


@pytest.mark.parametrize(
    "source",
    [
        "import os; os.system('unobserved')",
        "eval('1')",
        "exec('x=1')",
        "from pathlib import Path; Path('x').resolve().write_text('x')",
        "from pathlib import Path; p=Path('x'); getattr(p,'write_text')('x')",
    ],
)
def test_unsupported_python_never_executes_or_claims_a_read(tmp_path: Path, source: str) -> None:
    assert analyze_python(source, cwd=tmp_path).classification == "UNCLASSIFIED"
    assert not (tmp_path / "x").exists()


def test_standard_module_shadow_is_not_a_pure_import(tmp_path: Path) -> None:
    (tmp_path / "pathlib.py").write_text("raise RuntimeError('never execute')", encoding="utf-8")
    result = analyze_python("from pathlib import Path; Path('x').read_bytes()", cwd=tmp_path)
    assert result.classification == "UNCLASSIFIED"


def test_registered_read_helper_rejects_changed_arguments() -> None:
    prefix = ".venv/Scripts/python.exe scripts/check_module_budget.py"
    assert analyze_command(prefix, root=ROOT, cwd=ROOT).classification == "READ_ONLY"
    result = analyze_command(prefix + " --unexpected", root=ROOT, cwd=ROOT)
    assert result.classification == "UNCLASSIFIED"


@pytest.mark.parametrize("command", ["git -C . reset --hard", "git --work-tree . clean -fd"])
def test_unclassified_git_wrappers_do_not_escape_the_guard(tmp_path: Path, command: str) -> None:
    # Hook event only; never execute the destructive Git command.
    output = hook(command, tmp_path / "missing.json")
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("writing", [False, True])
def test_utf8_hook_event_is_independent_of_windows_console_encoding(
    tmp_path: Path,
    writing: bool,
) -> None:
    command = "rg -n '소유|공유' src/thoth/domain/actor.py"
    if writing:
        command += "; Set-Content -LiteralPath src/thoth/domain/base.py -Value '한글'"
    event = {
        "tool_name": "exec_command",
        "cwd": str(ROOT.parent),
        "tool_input": {"command": command, "workdir": str(ROOT)},
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / ".codex/hooks/pre_tool_policy.py")],
        input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        cwd=ROOT,
        check=False,
        env={
            **os.environ,
            "PYTHONUTF8": "0",
            "PYTHONIOENCODING": "cp949:surrogateescape",
            "THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json"),
        },
    )
    assert result.returncode == 0, result.stderr
    if writing:
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    else:
        assert result.stdout == b""


@pytest.mark.parametrize("payload", [b"\xff", b"[]"])
def test_malformed_hook_event_does_not_fail_open(payload: bytes) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / ".codex/hooks/pre_tool_policy.py")],
        input=payload,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unavailable_tool_workdir_requires_explicit_location_for_writes() -> None:
    command = (
        "Set-Content -LiteralPath tests/architecture/test_read_only_command_contract.py -Value x"
    )
    unresolved = analyze_command(command, root=ROOT, cwd=ROOT.parent, cwd_known=False)
    assert unresolved.classification == "UNCLASSIFIED"
    explicit = f"Set-Location -LiteralPath '{ROOT.as_posix()}'; " + command
    resolved = analyze_command(explicit, root=ROOT, cwd=ROOT.parent, cwd_known=False)
    assert resolved.classification == "WRITE"
    assert resolved.write_targets == (
        str(ROOT / "tests/architecture/test_read_only_command_contract.py"),
    )


def test_changed_registered_helper_hash_is_not_automatically_retrusted(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    for relative in (
        "scripts/parse_shell_units.ps1",
        "scripts/check_module_budget.py",
        "config/read-only-command-helpers.json",
    ):
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    helper = tmp_path / "scripts/check_module_budget.py"
    helper.write_text("raise RuntimeError('not executed')\n", encoding="utf-8")
    result = analyze_command(
        ".venv/Scripts/python.exe scripts/check_module_budget.py", root=tmp_path, cwd=tmp_path
    )
    assert result.classification == "UNCLASSIFIED"
    assert result.reason == "HELPER_HASH_DIFFERS"


def test_unknown_python_is_reported_as_unclassified(tmp_path: Path) -> None:
    output = hook(
        '.venv/Scripts/python.exe -c "import unknown_package; '
        "unknown_package.inspect('src/thoth/apps/runtime.py')\"",
        tmp_path / "missing.json",
    )
    assert output is not None
    assert (
        "UNCLASSIFIED_REQUIRES_REVIEW" in output["hookSpecificOutput"]["permissionDecisionReason"]
    )
