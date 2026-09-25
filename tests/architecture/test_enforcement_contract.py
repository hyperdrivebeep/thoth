from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.architecture.owner_helpers import bind_owner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.architecture_gate_contract import (  # noqa: E402
    archive_receipt,
    calculate_verification_receipt_id,
    scope_digest,
)
from scripts.verification_identity import index_digest, repository_digest  # noqa: E402


def _run(
    script: Path,
    *,
    value: dict[str, object] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        input=json.dumps({"session_id": "session-a", "turn_id": "turn-fixture", **(value or {})}),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def test_instruction_and_hook_contracts_are_present_and_parseable() -> None:
    assert (ROOT / "AGENTS.md").is_file()
    assert (ROOT / "PROJECT_WIKI/NOW.md").is_file()
    assert (ROOT / "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md").is_file()
    hooks = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart", "PreToolUse", "Stop"}
    assert hooks["hooks"]["PreToolUse"][0]["matcher"] == (
        "^(apply_patch|Edit|Write|Bash|exec_command|shell_command|shell)$"
    )
    manifest = json.loads(
        (ROOT / "config/architecture-conformance.json").read_text(encoding="utf-8")
    )
    assert manifest["rule_bundle"] == "thoth-architecture-rules@1.0.0"
    assert {item["id"] for item in manifest["acceptance_contracts"]} == {
        f"A{index:02d}" for index in range(1, 14)
    }


def test_pre_tool_hook_blocks_product_edit_without_preflight(tmp_path: Path) -> None:
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/thoth/domain/base.py\n"
            },
        },
        env={"THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json")},
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "path,protected", [("docs/architecture/error-contract.md", True), ("docs/report.md", False)]
)
def test_pre_tool_hook_distinguishes_frozen_rules_from_ordinary_docs(
    tmp_path: Path, path: str, protected: bool
) -> None:
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={
            "tool_name": "apply_patch",
            "tool_input": {"command": f"*** Begin Patch\n*** Update File: {path}\n"},
        },
        env={"THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json")},
    )
    assert result.returncode == 0
    if protected:
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    else:
        assert result.stdout == ""


def test_pre_tool_hook_blocks_python_write_without_preflight(tmp_path: Path) -> None:
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    'python -c "from pathlib import Path; '
                    "Path('src/thoth/domain/base.py').write_text('x')\""
                )
            },
        },
        env={"THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json")},
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    (
        "python -c \"open('src/thoth/domain/base.py', 'w').write('x')\"",
        "python -c \"import shutil; shutil.move('x', 'src/thoth/domain/base.py')\"",
        (
            'python -c "from pathlib import Path; print(1); '
            "Path('src/thoth/domain/base.py').touch()\""
        ),
        "node -e \"fs.writeFileSync('src/thoth/domain/base.py', 'x')\"",
        "[IO.File]::WriteAllText('src/thoth/domain/base.py', 'x')",
        "echo x > src/thoth/domain/base.py",
        "sed -i 's/a/b/' src/thoth/domain/base.py",
        "perl -pi -e 's/a/b/' src/thoth/domain/base.py",
        "git restore src/thoth/domain/base.py",
        "git reset --hard",
    ),
)
def test_pre_tool_hook_blocks_alternate_shell_mutations_without_preflight(
    tmp_path: Path,
    command: str,
) -> None:
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={"tool_name": "Bash", "tool_input": {"command": command}},
        env={"THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json")},
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_hook_allows_structured_read_without_preflight(tmp_path: Path) -> None:
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={
            "tool_name": "Bash",
            "tool_input": {"command": "Get-Content -LiteralPath src/thoth/domain/base.py"},
        },
        env={"THOTH_PREFLIGHT_PATH": str(tmp_path / "missing.json")},
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_pre_tool_hook_rejects_forged_preflight_receipt(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.json"
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "src/thoth/domain/base.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    forged = json.loads(preflight.read_text(encoding="utf-8"))
    forged["preflight_receipt_id"] = "f" * 64
    preflight.write_text(json.dumps(forged), encoding="utf-8")
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/thoth/domain/base.py\n"
            },
        },
        env={"THOTH_PREFLIGHT_PATH": str(preflight)},
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "invalid" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_stop_hook_requires_matching_verification_for_active_preflight(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.json"
    verification = tmp_path / "verification.json"
    preflight.write_text(
        json.dumps(
            {
                "status": "READY_FOR_EDIT",
                "preflight_receipt_id": "preflight:fixture",
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        ROOT / ".codex/hooks/stop_acceptance_gate.py",
        env={
            "THOTH_PREFLIGHT_PATH": str(preflight),
            "THOTH_VERIFICATION_PATH": str(verification),
        },
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["continue"] is False


def test_stop_hook_rejects_unverified_verified_pointer(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.json"
    verification = tmp_path / "verification.json"
    preflight.write_text(
        json.dumps(
            {
                "status": "VERIFIED",
                "preflight_receipt_id": "preflight:forged",
                "verification_receipt_id": "verification:forged",
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        ROOT / ".codex/hooks/stop_acceptance_gate.py",
        env={
            "THOTH_PREFLIGHT_PATH": str(preflight),
            "THOTH_VERIFICATION_PATH": str(verification),
        },
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["continue"] is False


def test_legacy_verified_receipts_do_not_become_owned_completion(tmp_path: Path) -> None:
    environment = {**os.environ, "THOTH_GATE_DIR": str(tmp_path)}
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "src/thoth/domain/base.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight_path = tmp_path / "preflight.json"
    verification_path = tmp_path / "verification.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    wiki_recorded = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/record_wiki_sync.py"),
            "--no-change-reason",
            "test fixture changes no product truth",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert wiki_recorded.returncode == 0, wiki_recorded.stderr
    wiki_sync = json.loads(wiki_recorded.stdout)
    verified_at = "2026-09-03T00:00:00+00:00"
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": preflight["acceptance_id"],
        "rule_bundle_digest": preflight["rule_bundle_digest"],
        "scope_digest": scope_digest(list(preflight["declared_scope"])),
        "wiki_sync_receipt_id": wiki_sync["wiki_sync_receipt_id"],
        "repository_digest": repository_digest(ROOT),
        "index_digest": index_digest(ROOT),
        "status": "PASS",
        "verified_at": verified_at,
    }
    verification_id = calculate_verification_receipt_id(draft)
    verification = {**draft, "verification_receipt_id": verification_id}
    preflight.update(
        {
            "status": "VERIFIED",
            "verified_at": verified_at,
            "verification_receipt_id": verification_id,
        }
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    verification_path.write_text(json.dumps(verification), encoding="utf-8")
    archive_receipt(
        tmp_path,
        receipt_id=verification_id,
        kind="verification",
        payload=verification,
    )

    result = _run(
        ROOT / ".codex/hooks/stop_acceptance_gate.py",
        env={
            "THOTH_PREFLIGHT_PATH": str(preflight_path),
            "THOTH_VERIFICATION_PATH": str(verification_path),
        },
    )
    assert result.returncode == 0
    assert "LEGACY_OWNER_UNBOUND" in result.stdout


def test_session_start_hook_injects_maturity_and_preflight_rules() -> None:
    result = _run(ROOT / ".codex/hooks/session_start.py")
    assert result.returncode == 0
    output = json.loads(result.stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "D0-D3 is not implementation completion" in context
    assert "READY_FOR_EDIT" in context


def test_a13_preflight_is_unblocked_after_authenticated_scope_remediation(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A13",
            "--scope",
            "src/thoth/adapters/http/app.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    preflight = json.loads((tmp_path / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["acceptance_id"] == "A13"
    assert preflight["planned_remediations"] == []
    assert preflight["status"] == "READY_FOR_EDIT"
    archived = tmp_path / "receipts" / f"{preflight['preflight_receipt_id']}.preflight.json"
    assert archived.is_file()
    assert json.loads(archived.read_text(encoding="utf-8")) == preflight


def test_unblocked_preflight_can_be_created_and_cancelled(tmp_path: Path) -> None:
    environment = {**os.environ, "THOTH_GATE_DIR": str(tmp_path)}
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A01",
            "--scope",
            "src/thoth/domain",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight = json.loads((tmp_path / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["status"] == "READY_FOR_EDIT"
    assert preflight["acceptance_id"] == "A01"

    cancelled = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/cancel_architecture_preflight.py"),
            "--reason",
            "architecture enforcement self-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert cancelled.returncode == 0, cancelled.stderr
    preflight = json.loads((tmp_path / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["status"] == "CANCELLED"
    cancellation_id = preflight["cancellation_receipt_id"]
    assert (tmp_path / "receipts" / f"{cancellation_id}.cancellation.json").is_file()
    stopped = _run(
        ROOT / ".codex/hooks/stop_acceptance_gate.py",
        env={
            "THOTH_PREFLIGHT_PATH": str(tmp_path / "preflight.json"),
            "THOTH_VERIFICATION_PATH": str(tmp_path / "verification.json"),
        },
    )
    assert stopped.returncode == 0
    assert stopped.stdout == ""


def test_shell_mutation_requires_every_target_to_be_inside_preflight_scope(
    tmp_path: Path,
) -> None:
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "src/thoth/domain/base.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    bind_owner(tmp_path)
    result = _run(
        ROOT / ".codex/hooks/pre_tool_policy.py",
        value={
            "tool_name": "Bash",
            "tool_input": {
                "command": ("ruff format src/thoth/domain/base.py src/thoth/domain/receipt.py")
            },
        },
        env={"THOTH_PREFLIGHT_PATH": str(tmp_path / "preflight.json")},
    )
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "src/thoth/domain/receipt.py" in output["hookSpecificOutput"]["permissionDecisionReason"]
