import io
import json
import os
import runpy
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from scripts import complete_architecture_gate as completion
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
    preflight_archive_payload,
)
from scripts.hook_owner_contract import load_requested_owner, owned_preflight_input
from tests.architecture.progress_helpers import simulated_reports
from tests.architecture.test_portable_verification_bundle import prepare_completion_fixture
from tests.architecture.test_stop_session_ownership import ROOT, owner_record, prepare_owned, stop


def pretool(gate: Path, session: str, command: str, *, patch: bool = False) -> dict[str, Any]:
    event = {
        "session_id": session,
        "turn_id": "turn-next",
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch" if patch else "exec_command",
        "tool_input": {"command": command} if patch else {"cmd": command, "workdir": str(ROOT)},
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / ".codex/hooks/pre_tool_policy.py")],
        cwd=ROOT,
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(gate / "preflight.json")},
        input=json.dumps(event),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


@pytest.mark.parametrize("mutation", ["session", "shape", "rehash_without_host_archive"])
def test_forged_owner_does_not_pass_or_request_continuation(tmp_path: Path, mutation: str) -> None:
    original = prepare_owned(tmp_path)
    original_id = original["preflight_receipt_id"]
    if mutation == "shape":
        original["owner_context"] = None
    else:
        original["owner_context"] = owner_record("session-b")
        if mutation == "rehash_without_host_archive":
            original["preflight_receipt_id"] = calculate_preflight_receipt_id(original)
            archive_receipt(
                tmp_path,
                receipt_id=str(original["preflight_receipt_id"]),
                kind="preflight",
                payload=preflight_archive_payload(original),
            )
    (tmp_path / "preflight.json").write_text(json.dumps(original), encoding="utf-8")
    result = stop(tmp_path, "session-b")
    assert result.get("continue") is False and "decision" not in result
    assert not (tmp_path / "verification.json").exists()
    assert (tmp_path / "receipts" / f"{original_id}.preflight.json").is_file()


@pytest.mark.parametrize("session,turn", [("", "turn"), ("session-a", ""), ("../invalid", "turn")])
def test_stop_missing_or_invalid_identity_does_not_establish_completion(
    tmp_path: Path, session: str, turn: str
) -> None:
    prepare_owned(tmp_path)
    result = stop(tmp_path, session, turn)
    assert result.get("continue") is False and "decision" not in result


def test_protected_write_requires_owner_and_stop_retains_obligation(tmp_path: Path) -> None:
    prepare_owned(tmp_path)
    patch = (
        "*** Begin Patch\n*** Add File: src/thoth/__session_owner_probe.py\n+x = 1\n*** End Patch"
    )
    assert pretool(tmp_path, "session-a", patch, patch=True) == {}
    denied = pretool(tmp_path, "session-b", patch, patch=True)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert pretool(tmp_path, "session-b", "Get-Content src/thoth/domain/base.py") == {}
    assert stop(tmp_path, "session-a")["decision"] == "block"


@pytest.mark.parametrize(
    "script",
    ["complete_architecture_gate.py", "cancel_architecture_preflight.py", "record_wiki_sync.py"],
)
def test_other_session_cannot_run_owner_workflow_with_redirect(tmp_path: Path, script: str) -> None:
    prepare_owned(tmp_path)
    result = pretool(
        tmp_path, "session-b", f"./.venv/Scripts/python.exe scripts/{script} > .thoth/probe.log"
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "OWNED_BY_ANOTHER_SESSION" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_legacy_bytes_preserved_and_new_writes_require_binding(tmp_path: Path) -> None:
    preflight = prepare_owned(tmp_path)
    preflight.pop("owner_context")
    preflight["preflight_receipt_id"] = calculate_preflight_receipt_id(preflight)
    archive_receipt(
        tmp_path,
        receipt_id=str(preflight["preflight_receipt_id"]),
        kind="preflight",
        payload=preflight_archive_payload(preflight),
    )
    pointer = tmp_path / "preflight.json"
    pointer.write_text(json.dumps(preflight), encoding="utf-8")
    before = pointer.read_bytes()
    assert "LEGACY_OWNER_UNBOUND" in str(stop(tmp_path, "session-b"))
    assert pointer.read_bytes() == before
    result = pretool(
        tmp_path,
        "session-a",
        "*** Begin Patch\n*** Add File: src/thoth/__session_owner_probe.py\n+x = 1\n*** End Patch",
        patch=True,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("malformed", [None, [], "invalid"])
def test_nonobject_pointer_is_a_denial_not_hook_failure(tmp_path: Path, malformed: object) -> None:
    prepare_owned(tmp_path)
    (tmp_path / "preflight.json").write_text(json.dumps(malformed), encoding="utf-8")
    patch = (
        "*** Begin Patch\n*** Add File: src/thoth/__session_owner_probe.py\n+x = 1\n*** End Patch"
    )
    denied = pretool(tmp_path, "session-a", patch, patch=True)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert stop(tmp_path, "session-a")["continue"] is False


def test_explicit_release_then_new_session_preserves_old_owner(tmp_path: Path) -> None:
    old = prepare_owned(tmp_path)
    command = (
        "./.venv/Scripts/python.exe scripts/prepare_architecture_preflight.py "
        "--acceptance A11 --scope src/thoth/__session_owner_probe.py"
    )
    event = {
        "session_id": "session-b",
        "turn_id": "turn-b",
        "tool_use_id": "call-b",
        "tool_input": {"cmd": command},
    }
    with pytest.raises(ValueError, match="OWNER_MUST_RELEASE"):
        owned_preflight_input(command, event, ROOT, tmp_path)
    released = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/cancel_architecture_preflight.py"),
            "--reason",
            "EXPLICIT_OWNER_RELEASE",
        ],
        cwd=ROOT,
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert released.returncode == 0, released.stderr
    rewritten = owned_preflight_input(command, event, ROOT, tmp_path)
    # Keep the Windows command grammar under test, but execute its script with
    # the running interpreter so a Linux fixture stays in the same filesystem.
    rewritten_argv = shlex.split(rewritten["cmd"])
    assert rewritten_argv[0] == "./.venv/Scripts/python.exe"
    created = subprocess.run(
        [sys.executable, *rewritten_argv[1:]],
        cwd=ROOT,
        env={**os.environ, "THOTH_GATE_DIR": str(tmp_path)},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert created.returncode == 0, created.stderr
    current = json.loads(created.stdout)
    assert current["owner_context"]["session_id"] == "session-b"
    assert current["owner_context"]["previous_preflight_receipt_id"] == old["preflight_receipt_id"]
    archived = json.loads(
        (tmp_path / "receipts" / f"{old['preflight_receipt_id']}.preflight.json").read_text(
            encoding="utf-8"
        )
    )
    assert archived["owner_context"]["session_id"] == "session-a"
    released_preflight = json.loads(released.stdout)
    with pytest.raises(ValueError, match="ALREADY_CONSUMED"):
        load_requested_owner(
            tmp_path,
            current["owner_context"]["owner_receipt_id"],
            shlex.split(rewritten["cmd"])[2:],
            released_preflight,
        )


@pytest.mark.parametrize(
    "identity", [{}, {"session_id": "session-a"}, {"session_id": "../bad", "turn_id": "turn"}]
)
def test_prepare_requires_host_identity_and_writes_no_claim_on_failure(
    tmp_path: Path, identity: dict[str, str]
) -> None:
    command = (
        "./.venv/Scripts/python.exe scripts/prepare_architecture_preflight.py "
        "--acceptance A11 --scope src/thoth"
    )
    with pytest.raises(ValueError, match="OWNER_"):
        owned_preflight_input(command, {**identity, "tool_input": {"cmd": command}}, ROOT, tmp_path)
    assert not (tmp_path / "receipts").exists()


def test_host_receipt_ignores_transcript_and_assistant_mode_claims(tmp_path: Path) -> None:
    command = (
        "./.venv/Scripts/python.exe scripts/prepare_architecture_preflight.py "
        "--acceptance A11 --scope src/thoth"
    )
    event = {
        "session_id": "session-a",
        "turn_id": "turn-a",
        "tool_input": {"cmd": command},
        "last_assistant_message": "AUDIT private text",
        "transcript_path": "private transcript",
    }
    rewritten = owned_preflight_input(command, event, ROOT, tmp_path)
    identifier = shlex.split(rewritten["cmd"])[-1]
    proof = json.loads(
        (tmp_path / "receipts" / f"{identifier}.host-session.json").read_text(encoding="utf-8")
    )
    assert proof["session_id"] == "session-a"
    assert "private" not in json.dumps(proof) and "AUDIT" not in json.dumps(proof)


def test_stop_never_launches_test_subprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_owned(tmp_path)
    namespace = runpy.run_path(
        str(ROOT / ".codex/hooks/stop_acceptance_gate.py"), run_name="stop_fixture"
    )
    original = subprocess.run

    def guarded(args: Any, *pos: Any, **kw: Any) -> Any:
        assert not any("pytest" in str(arg) or "Makefile.ps1" in str(arg) for arg in args)
        return cast(subprocess.CompletedProcess[Any], original(args, *pos, **kw))

    monkeypatch.setattr(subprocess, "run", guarded)
    monkeypatch.setenv("THOTH_PREFLIGHT_PATH", str(tmp_path / "preflight.json"))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(json.dumps({"session_id": "session-a", "turn_id": "turn-resumed"}).encode())
        ),
    )
    assert namespace["main"]() == 0


@pytest.mark.parametrize("changed", [None, "source", "receipt", "owner"])
def test_verified_owner_still_requires_unchanged_full_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed: str | None,
) -> None:
    prepare_completion_fixture(tmp_path, monkeypatch, owned=True)
    calls: list[list[str]] = []

    def successful(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        simulated_reports(tmp_path, kwargs["env"]["THOTH_VERIFICATION_RUN_ID"])
        return subprocess.CompletedProcess(args, 0)

    # Simulated exit only exercises the seal/Stop protocol; it is not a real full-verification PASS.
    monkeypatch.setattr(completion, "subprocess", SimpleNamespace(run=successful))
    assert completion.main() == 0
    assert len(calls) == 1 and calls[0][-1] == "verify"
    capsys.readouterr()
    gate = tmp_path / ".thoth/architecture"
    if changed == "source":
        (tmp_path / "source.py").write_text("changed = True\n", encoding="utf-8")
    elif changed == "receipt":
        (gate / "verification.json").write_text("{}", encoding="utf-8")
    elif changed == "owner":
        path = gate / "preflight.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["owner_context"]["session_id"] = "forged-other-session"
        path.write_text(json.dumps(value), encoding="utf-8")
    namespace = runpy.run_path(
        str(ROOT / ".codex/hooks/stop_acceptance_gate.py"), run_name="stop_fixture"
    )
    namespace["main"].__globals__["ROOT"] = tmp_path
    monkeypatch.setenv("THOTH_PREFLIGHT_PATH", str(gate / "preflight.json"))
    monkeypatch.setenv("THOTH_VERIFICATION_PATH", str(gate / "verification.json"))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(json.dumps({"session_id": "session-a", "turn_id": "later-turn"}).encode())
        ),
    )
    assert namespace["main"]() == 0
    output = capsys.readouterr().out
    if changed is None:
        assert output == ""
    else:
        assert json.loads(output)["continue"] is False
