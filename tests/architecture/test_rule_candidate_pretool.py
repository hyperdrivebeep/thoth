from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.rule_candidate_contract import validate_candidate
from tests.architecture.owner_helpers import bind_owner

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("bad_value", ["SqliteStoreFactory.open", "MissingFactory"])
def test_bad_consumer_is_rejected_before_active_rule_changes(bad_value: str) -> None:
    path = ROOT / "config/architecture-conformance.json"
    before = path.read_bytes()
    candidate = before.replace(
        b'thoth.adapters.storage.bundle.SqliteStoreFactory"',
        f'thoth.adapters.storage.bundle.{bad_value}"'.encode(),
    )
    assert candidate != before
    with pytest.raises(RuntimeError, match="SCHEMA_MIGRATION"):
        validate_candidate(ROOT, {"config/architecture-conformance.json": candidate})
    assert path.read_bytes() == before


def test_valid_rule_candidate_runs_all_seven_checks() -> None:
    relative = "config/architecture-conformance.json"
    results = validate_candidate(ROOT, {relative: (ROOT / relative).read_bytes() + b"\n"})
    assert len(results) == 7
    assert all(result["verdict"] == "PASS" for result in results)


def test_rule_pretool_denies_invalid_candidate_under_valid_preflight(tmp_path: Path) -> None:
    env = {**os.environ, "THOTH_GATE_DIR": str(tmp_path)}
    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_architecture_preflight.py",
            "--acceptance",
            "A11",
            "--scope",
            "config/architecture-conformance.json",
        ],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    bind_owner(tmp_path)
    line = next(
        line
        for line in (ROOT / "config/architecture-conformance.json").read_text().splitlines()
        if '"name": "SCHEMA_MIGRATION"' in line
    )
    bad = line.replace('SqliteStoreFactory"', 'SqliteStoreFactory.open"')
    patch = (
        "*** Begin Patch\n*** Update File: config/architecture-conformance.json\n@@\n"
        f"-{line}\n+{bad}\n*** End Patch"
    )
    hook = subprocess.run(
        [sys.executable, ".codex/hooks/pre_tool_policy.py"],
        cwd=ROOT,
        input=json.dumps(
            {
                "session_id": "session-a",
                "turn_id": "turn-fixture",
                "tool_name": "apply_patch",
                "tool_input": {"command": patch},
            }
        ),
        capture_output=True,
        encoding="utf-8",
        check=False,
        env={**env, "THOTH_PREFLIGHT_PATH": str(tmp_path / "preflight.json")},
    )
    output = json.loads(hook.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "RULE_CANDIDATE_REJECTED" in str(output)


def test_limit_raise_is_rejected_without_live_mutation() -> None:
    relative = "config/module-responsibility-budget.json"
    value = json.loads((ROOT / relative).read_bytes())
    value["function_line_limit"] += 1
    with pytest.raises(ValueError, match="size limits"):
        validate_candidate(ROOT, {relative: json.dumps(value).encode()})


def test_candidate_cannot_replace_the_checker_to_issue_itself_pass() -> None:
    relative = "config/architecture-conformance.json"
    before = (ROOT / relative).read_bytes()
    bad = before.replace(b'SqliteStoreFactory"', b'SqliteStoreFactory.open"')
    forged_checker = b'import json\nprint(json.dumps({"verdict":"PASS", "errors":[]}))\n'
    with pytest.raises(RuntimeError, match="SCHEMA_MIGRATION"):
        validate_candidate(
            ROOT,
            {
                relative: bad,
                "scripts/check_ocp_extensions.py": forged_checker,
            },
        )


def test_guard_code_edit_requires_a11_even_when_file_scope_is_present(tmp_path: Path) -> None:
    env = {**os.environ, "THOTH_GATE_DIR": str(tmp_path)}
    prepared = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_architecture_preflight.py",
            "--acceptance",
            "A01",
            "--scope",
            "scripts",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
    )
    assert prepared.returncode == 0
    bind_owner(tmp_path)
    hook = subprocess.run(
        [sys.executable, ".codex/hooks/pre_tool_policy.py"],
        cwd=ROOT,
        input=json.dumps(
            {
                "session_id": "session-a",
                "turn_id": "turn-fixture",
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Update File: scripts/check_architecture.py\n"
                },
            }
        ),
        encoding="utf-8",
        capture_output=True,
        check=False,
        env={**env, "THOTH_PREFLIGHT_PATH": str(tmp_path / "preflight.json")},
    )
    result = json.loads(hook.stdout)["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny" and "A11" in result["permissionDecisionReason"]
