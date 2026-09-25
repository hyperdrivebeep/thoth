from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.check_truth_drift import truth_drift_errors
from tests.architecture.owner_helpers import bind_owner

ROOT = Path(__file__).resolve().parents[2]
PAGES = {
    "NOW": "PROJECT_WIKI/NOW.md",
    "OCP": "PROJECT_WIKI/30_ARCHITECTURE/current-ocp-gaps.md",
    "maturity": "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md",
}


def run_scoped_shell(
    tmp_path: Path,
    command: str,
    scopes: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    arguments = [
        sys.executable,
        str(ROOT / "scripts/prepare_architecture_preflight.py"),
        "--acceptance",
        "A11",
    ]
    for scope in scopes:
        arguments.extend(("--scope", scope))
    prepared = subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr or prepared.stdout
    bind_owner(tmp_path)
    # Only the Hook subprocess receives this event; the shell command is never run.
    return subprocess.run(
        [sys.executable, str(ROOT / ".codex/hooks/pre_tool_policy.py")],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        input=json.dumps(
            {
                "session_id": "session-a",
                "turn_id": "turn-fixture",
                "tool_name": "exec_command",
                "tool_input": {"cmd": command},
            }
        ),
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(tmp_path / "preflight.json")},
        check=False,
    )


@pytest.mark.parametrize("path_form", ["relative", "windows", "absolute"])
def test_scoped_src_apps_path_does_not_require_a_phantom_apps_scope(
    tmp_path: Path,
    path_form: str,
) -> None:
    relative = "src/thoth/apps/guard_probe.py"
    path = {
        "relative": relative,
        "windows": relative.replace("/", "\\"),
        "absolute": str(ROOT / relative),
    }[path_form]
    result = run_scoped_shell(
        tmp_path,
        f"Set-Content -LiteralPath '{path}' -Value 'candidate'",
        (relative,),
    )
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize("outside_first", [True, False])
def test_separate_apps_occurrence_retains_scope_denial(
    tmp_path: Path,
    outside_first: bool,
) -> None:
    nested = "Set-Content src/thoth/apps/guard_probe.py candidate"
    outside = "Set-Content apps/guard_probe.py candidate"
    command = "; ".join((outside, nested) if outside_first else (nested, outside))
    result = run_scoped_shell(tmp_path, command, ("src/thoth/apps/guard_probe.py",))
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "apps/guard_probe.py" in output["permissionDecisionReason"]


@pytest.mark.parametrize("outside_text", ["apps/outside.py", "prefixapps/outside.py"])
def test_arbitrary_shell_content_still_requires_its_protected_scope(
    tmp_path: Path,
    outside_text: str,
) -> None:
    result = run_scoped_shell(
        tmp_path,
        "Set-Content -LiteralPath docs/report.md -Value "
        f"'source src/thoth/apps/guard_probe.py and {outside_text}'",
        ("src/thoth/apps/guard_probe.py",),
    )
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "apps/outside.py" in output["permissionDecisionReason"]


def test_genuine_apps_target_is_allowed_only_with_its_own_scope(tmp_path: Path) -> None:
    result = run_scoped_shell(
        tmp_path,
        "Set-Content src/thoth/apps/guard_probe.py candidate; "
        "Set-Content apps/guard_probe.py candidate",
        ("src/thoth/apps/guard_probe.py", "apps/guard_probe.py"),
    )
    assert result.returncode == 0
    assert result.stdout == ""


def debt_fixture(root: Path, *, count: int = 9) -> None:
    manifest = {
        "extension_points": [
            {"name": name, "status": "IMPLEMENTED"} for name in ("PARSER", "CONNECTOR", "SANDBOX")
        ],
        "canonical_owners": [
            {
                "aggregate": f"OWNER_{number}",
                "current_atomicity": "PARTIAL",
                "atomicity_debt": {
                    "debt_id": f"DEBT-{number}",
                    "status": "OPEN",
                    "acceptance_ids": ["A11"],
                    "closure_evidence_required": "fault-injection receipt",
                },
            }
            for number in range(count)
        ],
    }
    config = root / "config/architecture-conformance.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(manifest), encoding="utf-8")
    for relative in PAGES.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "OCP-REGISTRY-001 OCP-FACTORY-001 OCP-CORE-CLOSED-001 OCP-DOC-DRIFT-001\n"
            f"ATOMICITY DEBT: {count} OPEN\n"
            "Historical checkpoint: ATOMICITY DEBT: 10 OPEN\n"
            "Previous narrative recorded ten OPEN owner debts.\n",
            encoding="utf-8",
        )


def test_manifest_and_matrix_nine_cannot_hide_now_and_ocp_ten(tmp_path: Path) -> None:
    debt_fixture(tmp_path, count=9)
    for label in ("NOW", "OCP"):
        path = tmp_path / PAGES[label]
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "ATOMICITY DEBT: 9 OPEN",
                "ATOMICITY DEBT: 10 OPEN",
            ),
            encoding="utf-8",
        )
    errors = truth_drift_errors(tmp_path)
    assert any("NOW" in error and "differs from 9 OPEN" in error for error in errors)
    assert any("OCP" in error and "differs from 9 OPEN" in error for error in errors)


@pytest.mark.parametrize("count", [0, 9, 10])
def test_matching_current_debt_markers_allow_unchanged_historical_prose(
    tmp_path: Path,
    count: int,
) -> None:
    debt_fixture(tmp_path, count=count)
    before = {label: (tmp_path / path).read_bytes() for label, path in PAGES.items()}
    assert truth_drift_errors(tmp_path) == []
    assert before == {label: (tmp_path / path).read_bytes() for label, path in PAGES.items()}


@pytest.mark.parametrize("label", ["NOW", "OCP", "maturity"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "stale"])
def test_each_current_projection_requires_one_exact_matching_marker(
    tmp_path: Path,
    label: str,
    mutation: str,
) -> None:
    debt_fixture(tmp_path)
    path = tmp_path / PAGES[label]
    original = "ATOMICITY DEBT: 9 OPEN\n"
    replacement = {
        "missing": "",
        "duplicate": original + original,
        "stale": "ATOMICITY DEBT: 10 OPEN\n",
    }[mutation]
    path.write_text(
        path.read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )
    assert any(
        label in error and "atomicity debt projection" in error
        for error in truth_drift_errors(tmp_path)
    )


def test_repository_current_debt_markers_are_all_checked() -> None:
    assert truth_drift_errors(ROOT) == []
