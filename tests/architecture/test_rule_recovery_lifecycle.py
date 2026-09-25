from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.rule_candidate_contract import apply_candidate_patch
from scripts.stop_progress_contract import stop_response


def test_candidate_patch_rejects_ambiguous_context_and_preserves_input() -> None:
    original = b"a\na\n"
    with pytest.raises(ValueError, match="ambiguous"):
        apply_candidate_patch(original, "@@\n-a\n+b\n")
    assert original == b"a\na\n"


def test_candidate_patch_preserves_crlf_and_requires_exact_context() -> None:
    assert apply_candidate_patch(b"a\r\nb\r\n", "@@\n a\n-b\n+c\n") == b"a\r\nc\r\n"
    with pytest.raises(ValueError):
        apply_candidate_patch(b"a\nb\n", "@@\n-missing\n+c\n")


def test_stop_continues_unfinished_work_but_bounds_no_progress(tmp_path: Path) -> None:
    event = {"session_id": "unit-session", "stop_hook_active": False}
    first = stop_response(tmp_path, event, "CAN_CONTINUE", "run verification", "node-a")
    assert first == {"decision": "block", "reason": "run verification"}
    repeated = stop_response(tmp_path, event, "CAN_CONTINUE", "run verification", "node-a")
    assert repeated["continue"] is False
    assert "NO_PROGRESS" in str(repeated)
    progress = stop_response(tmp_path, event, "CAN_CONTINUE", "next node", "node-b")
    assert progress["decision"] == "block"


def test_stop_invalid_state_is_actionable_hold_not_continuation(tmp_path: Path) -> None:
    result = stop_response(tmp_path, {}, "OWNER_REQUIRED", "invalid rule snapshot", "bad")
    assert result["continue"] is False
    assert "OWNER_REQUIRED" in result["systemMessage"]
    assert "decision" not in result
    assert stop_response(tmp_path, {}, "DONE", "", "ok") == {}


def test_whole_plan_and_node_completion_are_separate() -> None:
    from scripts.stop_progress_contract import next_plan_node

    root = Path(__file__).resolve().parents[2]
    assert next_plan_node(root, {
        "plan_id": "THOTH-NEXT-20260906", "node_id": "A11-RECOVERY"
    }) == "N08"
    assert next_plan_node(root, {
        "plan_id": "THOTH-NEXT-20260906", "node_id": "N08"
    }) == "N07"
    assert next_plan_node(root, {
        "plan_id": "THOTH-CHECKER-20260908", "node_id": "A11-RECOVERY"
    }) is None
    with pytest.raises(ValueError, match="unregistered plan"):
        next_plan_node(root, {"plan_id": "unregistered", "node_id": "N12"})


def test_preflight_reissue_is_not_progress_and_continuations_have_a_hard_cap(
    tmp_path: Path,
) -> None:
    from scripts.stop_progress_contract import MAX_CONTINUATIONS, progress_fingerprint

    first = progress_fingerprint(
        {"plan_id": "P1", "node_id": "N01", "preflight_receipt_id": "a"}, "s"
    )
    again = progress_fingerprint(
        {"plan_id": "P1", "node_id": "N01", "preflight_receipt_id": "b"}, "s"
    )
    assert first == again
    for index in range(MAX_CONTINUATIONS):
        result = stop_response(tmp_path, {}, "CAN_CONTINUE", "verify", str(index))
        assert result["decision"] == "block"
    response = stop_response(tmp_path, {}, "CAN_CONTINUE", "verify", "new-source")
    assert "BUDGET_EXHAUSTED" in response["systemMessage"]


def test_parent_workspace_patch_prefix_targets_the_repository(tmp_path: Path) -> None:
    from scripts.rule_candidate_contract import patch_candidates

    root = tmp_path / "thoth-prototype"
    (root / "config").mkdir(parents=True)
    (root / "config/sample.json").write_bytes(b"old\n")
    patch = (
        "*** Begin Patch\n*** Update File: thoth-prototype/config/sample.json\n"
        "@@\n-old\n+new\n*** End Patch"
    )
    assert patch_candidates(root, patch) == {"config/sample.json": b"new\n"}


def test_cancel_stale_preflight_preserves_archive_without_granting_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import architecture_gate_contract as contract
    from scripts import cancel_architecture_preflight as cancel
    from scripts.prepare_architecture_preflight import main as prepare

    monkeypatch.setattr("scripts.prepare_architecture_preflight.GATE_DIR", tmp_path)
    monkeypatch.setattr(
        "scripts.prepare_architecture_preflight.PREFLIGHT", tmp_path / "preflight.json"
    )
    monkeypatch.setattr("sys.argv", ["prepare", "--acceptance", "A11", "--scope", "scripts"])
    assert prepare() == 0
    path = tmp_path / "preflight.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    archive = tmp_path / "receipts" / f"{before['preflight_receipt_id']}.preflight.json"
    original = archive.read_bytes()
    def changed_rules(manifest: object) -> str:
        return "0" * 64

    monkeypatch.setattr(contract, "rule_bundle_digest", changed_rules)
    monkeypatch.setattr(cancel, "GATE_DIR", tmp_path)
    monkeypatch.setattr(cancel, "PREFLIGHT", path)
    monkeypatch.setattr("sys.argv", ["cancel", "--reason", "rule drift; no product acceptance"])
    assert cancel.main() == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["status"] == "CANCELLED"
    assert archive.read_bytes() == original
    with pytest.raises(ValueError):
        contract.validate_preflight(after, allowed_statuses=frozenset({"READY_FOR_EDIT"}))
