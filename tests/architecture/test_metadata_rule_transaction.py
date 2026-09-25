from __future__ import annotations

import json

import pytest
from scripts.rule_transaction_contract import METADATA_PATHS, validate_metadata_change
from scripts.rule_transaction_engine import MetadataRuleTransaction
from tests.architecture.rule_transaction_helpers import prepare, rule_repository, run_cli


def test_normal_cli_stage_apply_and_fresh_preflight_with_no_replay() -> None:
    with rule_repository() as root:
        old = prepare(root)
        gate = root / ".thoth/architecture"
        candidate = root / ".thoth/rule-candidates/format.json"
        candidate.parent.mkdir(parents=True)
        relative = "config/architecture-conformance.json"
        before = (root / relative).read_text(encoding="utf-8")
        candidate.write_text(json.dumps({"files": {relative: before + "\n"}}), encoding="utf-8")
        staged = run_cli(root, "change_architecture_rules", "stage", "--candidate", str(candidate))
        assert staged.returncode == 0, staged.stderr
        identity = json.loads(staged.stdout)["proposal_id"]
        preview = json.loads(staged.stdout)
        assert "config" in preview["approved_scope"]
        assert preview["changes"][0]["path"] == relative
        assert len(preview["source_manifest_digest"]) == 64
        assert (root / relative).read_text(encoding="utf-8") == before
        applied = run_cli(
            root,
            "change_architecture_rules",
            "apply",
            "--proposal",
            identity,
            "--approve-proposal",
            identity,
        )
        assert applied.returncode == 0, applied.stderr
        result = json.loads(applied.stdout)
        assert result["status"] == "APPLIED" and result["product_accepted"] is False
        renewed = json.loads((gate / "preflight.json").read_bytes())
        assert renewed["preflight_receipt_id"] != old["preflight_receipt_id"]
        assert renewed["rule_change_proposal_id"] == identity
        assert all(check["verdict"] == "PASS" for check in renewed["baseline_checks"])
        replay = run_cli(
            root,
            "change_architecture_rules",
            "apply",
            "--proposal",
            identity,
            "--approve-proposal",
            identity,
        )
        assert replay.returncode != 0


@pytest.mark.parametrize(
    "point",
    [
        "journal",
        "file:config/architecture-conformance.json",
        "file:config/module-responsibility-budget.json",
        "validated",
        "renewed",
        "sealed",
        "consumed",
    ],
)
def test_every_apply_boundary_has_a_resumable_exact_pending_transaction(point: str) -> None:
    with rule_repository() as root:
        prepare(root)
        gate = root / ".thoth/architecture"
        engine = MetadataRuleTransaction(root, gate)
        candidates = {name: (root / name).read_bytes() + b"\n" for name in METADATA_PATHS}
        proposal = engine.stage(candidates)

        def fail(stage: str) -> None:
            if stage == point:
                raise RuntimeError("injected transaction interruption")

        broken = MetadataRuleTransaction(root, gate, fail)
        with pytest.raises(RuntimeError, match="interruption"):
            broken.apply(proposal.identity, proposal.identity)
        assert json.loads(engine.pending.read_bytes()) == {"proposal_id": proposal.identity}
        result = engine.resume(proposal.identity, proposal.identity)
        assert result["status"] == "APPLIED"
        assert not engine.pending.exists()
        for change in proposal.changes:
            assert (root / change.path).read_bytes() == change.bytes("after")


def test_partial_transaction_rolls_back_only_its_exact_bytes() -> None:
    with rule_repository() as root:
        old = prepare(root)
        gate = root / ".thoth/architecture"
        engine = MetadataRuleTransaction(root, gate)
        candidates = {name: (root / name).read_bytes() + b"\n" for name in METADATA_PATHS}
        proposal = engine.stage(candidates)

        def fail(stage: str) -> None:
            if stage.startswith("file:"):
                raise RuntimeError("injected")

        with pytest.raises(RuntimeError):
            MetadataRuleTransaction(root, gate, fail).apply(proposal.identity, proposal.identity)
        result = engine.rollback(proposal.identity, proposal.identity)
        assert result["status"] == "ROLLED_BACK" and result["product_accepted"] is False
        assert json.loads((gate / "preflight.json").read_bytes()) == old
        for change in proposal.changes:
            assert (root / change.path).read_bytes() == change.bytes("before")


def test_source_drift_and_wrong_approval_reject_without_writing() -> None:
    with rule_repository() as root:
        prepare(root)
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        relative = "config/architecture-conformance.json"
        before = (root / relative).read_bytes()
        proposal = engine.stage({relative: before + b"\n"})
        with pytest.raises(ValueError, match="approval"):
            engine.apply(proposal.identity, "0" * 64)
        changed = root / "user-change.txt"
        changed.write_text("new user work", encoding="utf-8")
        with pytest.raises(ValueError, match="inventory changed"):
            engine.apply(proposal.identity, proposal.identity)
        assert (root / relative).read_bytes() == before and not engine.pending.exists()
        assert changed.read_text() == "new user work"


def test_guard_and_authority_changes_are_outside_metadata_repair() -> None:
    with pytest.raises(ValueError, match="cannot change this file"):
        validate_metadata_change(".codex/hooks/pre_tool_policy.py", b"{}", b"{}")
    with rule_repository() as root:
        path = "config/architecture-conformance.json"
        before = (root / path).read_bytes()
        value = json.loads(before)
        value["known_exceptions"] = [{"id": "NEW-EXCEPTION"}]
        with pytest.raises(ValueError, match="owners, authority"):
            validate_metadata_change(path, before, json.dumps(value).encode())
        value = json.loads(before)
        migration = next(
            item for item in value["extension_points"] if item["name"] == "SCHEMA_MIGRATION"
        )
        del migration["consumer_targets"]
        with pytest.raises(ValueError, match="consumer declarations"):
            validate_metadata_change(path, before, json.dumps(value).encode())


def test_failure_evidence_cannot_be_relabelled_pass_even_with_a_new_digest() -> None:
    from dataclasses import replace

    from scripts.rule_transaction_contract import archive_proposal

    with rule_repository() as root:
        prepare(root)
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        relative = "config/architecture-conformance.json"
        before = (root / relative).read_bytes()
        proposal = engine.stage({relative: before + b"\n"})
        checks = [{**item} for item in proposal.checks]
        checks[0]["exit_code"] = 1
        invalid = replace(proposal, checks=checks)
        archive_proposal(engine.gate, invalid)
        with pytest.raises(ValueError, match="skipped or failed"):
            engine.apply(invalid.identity, invalid.identity)
        assert (root / relative).read_bytes() == before and not engine.pending.exists()


def test_index_drift_and_corrupt_journal_preserve_current_files() -> None:
    import subprocess

    with rule_repository() as root:
        prepare(root)
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        relative = "config/architecture-conformance.json"
        before = (root / relative).read_bytes()
        proposal = engine.stage({relative: before + b"\n"})
        result = subprocess.run(["git", "add", "AGENTS.md"], cwd=root, check=False)
        assert result.returncode == 0
        with pytest.raises(ValueError, match="index_digest"):
            engine.apply(proposal.identity, proposal.identity)
        engine.pending.write_text(json.dumps({"proposal_id": "0" * 64}), encoding="utf-8")
        with pytest.raises(ValueError, match="pending transaction differs"):
            engine.resume(proposal.identity, proposal.identity)
        assert (root / relative).read_bytes() == before


def test_201_to_193_watch_retirement_uses_candidate_then_transaction() -> None:
    with rule_repository() as root:
        relative = "src/thoth/domain/ratchet_fixture.py"
        source = root / relative
        source.write_text("def ratchet_fixture():\n" + "    pass\n" * 200, encoding="utf-8")
        path = "config/module-responsibility-budget.json"
        budget = json.loads((root / path).read_bytes())
        key = relative + "::ratchet_fixture"
        budget["watched_long_functions"][key] = 201
        (root / path).write_text(json.dumps(budget), encoding="utf-8")
        prepare(root)
        source.write_text("def ratchet_fixture():\n" + "    pass\n" * 192, encoding="utf-8")
        old = (root / path).read_bytes()
        del budget["watched_long_functions"][key]
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        proposal = engine.stage({path: json.dumps(budget).encode()})
        assert (root / path).read_bytes() == old
        engine.apply(proposal.identity, proposal.identity)
        updated = json.loads((root / path).read_bytes())
        assert key not in updated["watched_long_functions"]
        assert updated["function_line_limit"] == 200 and updated["module_line_limit"] == 900


def test_head_drift_rejects_before_metadata_write() -> None:
    import subprocess

    with rule_repository() as root:
        prepare(root)
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        relative = "config/architecture-conformance.json"
        before = (root / relative).read_bytes()
        proposal = engine.stage({relative: before + b"\n"})
        parent = subprocess.run(
            ["git", "rev-parse", "HEAD^"], cwd=root, capture_output=True, check=False
        )
        assert parent.returncode == 0
        changed = subprocess.run(
            ["git", "update-ref", "HEAD", parent.stdout.decode().strip()], cwd=root, check=False
        )
        assert changed.returncode == 0  # Disposable clone only, never the user's repository.
        with pytest.raises(ValueError, match="head"):
            engine.apply(proposal.identity, proposal.identity)
        assert (root / relative).read_bytes() == before and not engine.pending.exists()


def test_known_pending_transaction_prompts_bounded_resume_not_owner_approval() -> None:
    import subprocess
    import sys

    from tests.architecture.owner_helpers import bind_owner

    with rule_repository() as root:
        prepare(root)
        bind_owner(root / ".thoth/architecture", session="pending-test")
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        relative = "config/architecture-conformance.json"
        proposal = engine.stage({relative: (root / relative).read_bytes() + b"\n"})

        def fail(stage: str) -> None:
            if stage == "journal":
                raise RuntimeError("injected")

        with pytest.raises(RuntimeError):
            MetadataRuleTransaction(root, engine.gate, fail).apply(
                proposal.identity, proposal.identity
            )
        response = subprocess.run(
            [sys.executable, ".codex/hooks/stop_acceptance_gate.py"],
            cwd=root,
            input=json.dumps({"session_id": "pending-test", "turn_id": "turn-fixture"}),
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        assert response.returncode == 0, response.stderr
        assert json.loads(response.stdout)["decision"] == "block"
        assert proposal.identity in response.stdout
        assert engine.resume(proposal.identity, proposal.identity)["status"] == "APPLIED"


def test_unknown_drift_during_recovery_never_overwrites_user_changes() -> None:
    with rule_repository() as root:
        prepare(root)
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        candidates = {name: (root / name).read_bytes() + b"\n" for name in METADATA_PATHS}
        proposal = engine.stage(candidates)

        def fail(stage: str) -> None:
            if stage.startswith("file:"):
                raise RuntimeError("injected")

        with pytest.raises(RuntimeError):
            MetadataRuleTransaction(root, engine.gate, fail).apply(
                proposal.identity, proposal.identity
            )
        observed = {name: (root / name).read_bytes() for name in METADATA_PATHS}
        (root / "new-user-work.txt").write_text("preserve me", encoding="utf-8")
        for action in (engine.resume, engine.rollback):
            with pytest.raises(ValueError, match="inventory changed"):
                action(proposal.identity, proposal.identity)
        assert observed == {name: (root / name).read_bytes() for name in METADATA_PATHS}
        assert (root / "new-user-work.txt").read_text() == "preserve me"


def test_missing_publication_cannot_be_laundered_into_prepare_or_completion() -> None:
    with rule_repository() as root:
        # A regression must fail quickly, not recursively launch another full test suite.
        (root / "Makefile.ps1").write_text(
            'Set-Content -LiteralPath (Join-Path $PSScriptRoot "unexpected-verify.txt") '
            '-Value "unexpected"\nexit 77\n',
            encoding="utf-8",
        )
        prepare(root)
        engine = MetadataRuleTransaction(root, root / ".thoth/architecture")
        relative = "config/architecture-conformance.json"
        proposal = engine.stage({relative: (root / relative).read_bytes() + b"\n"})

        def fail(stage: str) -> None:
            if stage == "renewed":
                raise RuntimeError("injected")

        with pytest.raises(RuntimeError):
            MetadataRuleTransaction(root, engine.gate, fail).apply(
                proposal.identity, proposal.identity
            )
        # Simulate a lost journal in the disposable clone; never fabricate a completion.
        engine.pending.unlink()
        pointer = engine.pointer.read_bytes()
        fresh = run_cli(
            root, "prepare_architecture_preflight", "--acceptance", "A11", "--scope", "config"
        )
        assert fresh.returncode != 0
        completed = run_cli(root, "complete_architecture_gate")
        assert completed.returncode != 0
        assert "rule-transactions" in completed.stderr
        assert not (root / "unexpected-verify.txt").exists()
        assert engine.pointer.read_bytes() == pointer
        assert not (engine.gate / "verification.json").exists()
