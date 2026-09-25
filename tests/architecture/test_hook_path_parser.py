from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.architecture.owner_helpers import bind_owner

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".codex/hooks/pre_tool_policy.py"


def _run_hook(
    tmp_path: Path,
    *,
    patch: str,
    scopes: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    preflight = tmp_path / "preflight.json"
    arguments = [
        sys.executable,
        str(ROOT / "scripts/prepare_architecture_preflight.py"),
        "--acceptance",
        "A01",
    ]
    for scope in scopes:
        arguments.extend(("--scope", scope))
    prepared = subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    bind_owner(tmp_path)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=ROOT,
        input=json.dumps(
            {
                "tool_name": "apply_patch",
                "tool_input": {"command": patch},
                "session_id": "session-a",
                "turn_id": "turn-fixture",
            }
        ),
        capture_output=True,
        text=True,
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(preflight)},
        check=False,
    )


def _run_without_preflight(*, patch: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=ROOT,
        input=json.dumps(
            {
                "tool_name": "apply_patch",
                "tool_input": {"command": patch},
            }
        ),
        capture_output=True,
        text=True,
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json")},
        check=False,
    )


def test_exact_scoped_absolute_windows_path_with_n_is_allowed(tmp_path: Path) -> None:
    relative = "tests/integration/test_thread_analysis_failure_diagnostics.py"
    target = str(ROOT / relative)
    result = _run_hook(
        tmp_path,
        patch=f"*** Begin Patch\n*** Update File: {target}\n*** End Patch\n",
        scopes=(relative,),
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_parent_workspace_relative_path_is_rebased_to_repo(tmp_path: Path) -> None:
    relative = "tests/integration/test_thread_analysis_failure_diagnostics.py"
    result = _run_hook(
        tmp_path,
        patch=(f"*** Begin Patch\n*** Update File: {ROOT.name}/{relative}\n*** End Patch\n"),
        scopes=(relative,),
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_out_of_scope_patch_reports_the_complete_path(tmp_path: Path) -> None:
    outside = "src/thoth/domain/base.py"
    result = _run_hook(
        tmp_path,
        patch=(
            "*** Begin Patch\n"
            "*** Update File: tests/integration/allowed.py\n"
            f"*** Update File: {outside}\n"
            "*** End Patch\n"
        ),
        scopes=("tests/integration",),
    )

    output = json.loads(result.stdout)
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert outside in reason


def test_rule_document_can_mention_product_paths_without_becoming_product_edit(
    tmp_path: Path,
) -> None:
    result = _run_without_preflight(
        tmp_path=tmp_path,
        patch=(
            "*** Begin Patch\n"
            "*** Update File: docs/architecture/example.md\n"
            "+The owner is src/thoth/application/services/example.py.\n"
            "*** End Patch\n"
        ),
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_parent_traversal_cannot_escape_declared_scope(tmp_path: Path) -> None:
    result = _run_hook(
        tmp_path,
        patch=(
            "*** Begin Patch\n"
            "*** Update File: tests/integration/../unit/escaped.py\n"
            "*** End Patch\n"
        ),
        scopes=("tests/integration",),
    )

    output = json.loads(result.stdout)
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "tests/integration/../unit/escaped.py" in reason
