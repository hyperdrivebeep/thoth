"""Journaled, exact-digest metadata transitions; no guard-code or authority repair lane."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.architecture_contract import load_manifest, rule_bundle_digest, rule_bundle_paths
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
    preflight_archive_payload,
    validate_archived_receipt,
    validate_preflight_identity,
)
from scripts.required_architecture_checks import run_checks
from scripts.rule_candidate_contract import validate_candidate
from scripts.rule_recovery_contract import capture_snapshot
from scripts.rule_transaction_contract import (
    RuleChangeProposal,
    RuleFileChange,
    archive_proposal,
    checked_id,
    digest,
    load_proposal,
    require_basis,
    safe_metadata,
    validate_metadata_change,
)
from scripts.verification_identity import capture_source_manifest


class MetadataRuleTransaction:
    def __init__(
        self, root: Path, gate: Path, fault: Callable[[str], None] | None = None
    ) -> None:
        if (
            root.resolve() != root.absolute() or gate.resolve() != gate.absolute()
            or not gate.is_relative_to(root / ".thoth")
        ):
            raise ValueError("transaction root/gate must be regular in-repository paths")
        self.root, self.gate = root, gate
        self.pointer = gate / "preflight.json"
        self.pending = gate / "pending-rule-change.json"
        self.fault = fault or (lambda stage: None)

    def _preflight(self) -> dict[str, Any]:
        value = json.loads(self.pointer.read_bytes())
        validate_preflight_identity(value, allowed_statuses=frozenset({"READY_FOR_EDIT"}))
        validate_archived_receipt(
            self.gate, receipt_id=value["preflight_receipt_id"], kind="preflight",
            payload=preflight_archive_payload(value),
        )
        return value

    def _write(self, target: Path, data: bytes) -> None:
        self.gate.mkdir(parents=True, exist_ok=True)
        temporary = self.gate / "metadata-transaction-write.tmp"
        if temporary.is_symlink() or temporary.resolve() != temporary.absolute():
            raise ValueError("linked transaction temporary path is forbidden")
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def _json(self, target: Path, value: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write(target, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())

    def stage(self, candidates: dict[str, bytes]) -> RuleChangeProposal:
        if self.pending.exists() or (self.gate / "pending-rule-restore.json").exists():
            raise ValueError("a rule transaction is pending")
        preflight = self._preflight()
        rules = rule_bundle_digest(load_manifest(self.root), self.root)
        if preflight["rule_bundle_digest"] != rules:
            raise ValueError("legacy rule drift has no transaction; owner correction required")
        changes = []
        if not candidates or len(candidates) > 2:
            raise ValueError("metadata proposal must contain one or two allowed files")
        for relative, after in sorted(candidates.items()):
            before = safe_metadata(self.root, relative).read_bytes()
            if not any(
                relative == scope or relative.startswith(scope.rstrip("/") + "/")
                for scope in preflight["declared_scope"]
            ):
                raise ValueError("metadata file is outside the approved preflight scope")
            validate_metadata_change(relative, before, after)
            if before != after:
                changes.append(RuleFileChange(
                    relative, base64.b64encode(before).decode(), base64.b64encode(after).decode()
                ))
        if not changes:
            raise ValueError("empty metadata change")
        basis = capture_source_manifest(self.root)
        checks = validate_candidate(self.root, candidates)
        proposal = RuleChangeProposal(
            preflight["preflight_receipt_id"], rules, basis, tuple(changes), checks,
            approved_scope=tuple(preflight["declared_scope"]),
        )
        require_basis(self.root, proposal, allow_after=False)
        if self._preflight() != preflight:
            raise ValueError("preflight changed during candidate validation")
        archive_proposal(self.gate, proposal)
        return proposal

    def _used_path(self, identity: str) -> Path:
        return self.gate / "rule-transactions" / f"{checked_id(identity)}.json"

    def _authorize(self, identity: str, approval: str, *, resume: bool) -> RuleChangeProposal:
        if checked_id(identity) != approval:
            raise ValueError("exact proposal approval does not match")
        proposal = load_proposal(self.gate, identity)
        if tuple(self._preflight()["declared_scope"]) != proposal.approved_scope:
            raise ValueError("approved transaction scope changed")
        if resume:
            pending = json.loads(self.pending.read_bytes())
            if pending != {"proposal_id": identity}:
                raise ValueError("pending transaction differs or is corrupt")
        else:
            if self.pending.exists() or self._used_path(identity).exists():
                raise ValueError("transaction pending or approval already consumed")
            current = self._preflight()
            if current["preflight_receipt_id"] != proposal.preflight_receipt_id:
                raise ValueError("active preflight changed after proposal")
            current_rules = rule_bundle_digest(load_manifest(self.root), self.root)
            if current_rules != proposal.rule_bundle_digest:
                raise ValueError("live rule basis changed")
        require_basis(self.root, proposal, allow_after=resume)
        return proposal

    def apply(self, identity: str, approval: str) -> dict[str, Any]:
        proposal = self._authorize(identity, approval, resume=False)
        self._json(self.pending, {"proposal_id": identity})
        self.fault("journal")
        return self._forward(proposal)

    def inspect_pending(self) -> dict[str, str]:
        identity = json.loads(self.pending.read_bytes())["proposal_id"]
        proposal = self._authorize(identity, identity, resume=True)
        current = self._preflight()
        if (
            current["preflight_receipt_id"] != proposal.preflight_receipt_id
            and current.get("rule_change_proposal_id") != identity
        ):
            raise ValueError("pending transaction lost its preflight authority")
        progress = {
            "proposal": identity, "preflight": current["preflight_receipt_id"],
            "files": {
                change.path: safe_metadata(self.root, change.path).read_text(encoding="utf-8")
                for change in proposal.changes
            },
        }
        return {"proposal_id": identity, "progress_id": digest(progress)}

    def resume(self, identity: str, approval: str) -> dict[str, Any]:
        proposal = self._authorize(identity, approval, resume=True)
        used = self._used_path(identity)
        if used.exists():
            result = json.loads(used.read_bytes())
            validate_archived_receipt(
                self.gate, receipt_id=digest(result), kind="rule-change-result", payload=result
            )
            expected = result.get("new_preflight_receipt_id") or proposal.preflight_receipt_id
            if self._preflight()["preflight_receipt_id"] != expected:
                raise ValueError("preflight changed after transaction publication")
            self.pending.unlink()
            return result
        return self._forward(proposal)

    def _forward(self, proposal: RuleChangeProposal) -> dict[str, Any]:
        current = self._preflight()
        if current["preflight_receipt_id"] != proposal.preflight_receipt_id:
            if current.get("rule_change_proposal_id") != proposal.identity:
                raise ValueError("another preflight replaced the pending transaction")
            for change in proposal.changes:
                if safe_metadata(self.root, change.path).read_bytes() != change.bytes("after"):
                    raise ValueError("renewed preflight does not match committed metadata")
            return self._close(proposal, "APPLIED", current["preflight_receipt_id"])
        for change in proposal.changes:
            require_basis(self.root, proposal, allow_after=True)
            target = safe_metadata(self.root, change.path)
            if target.read_bytes() != change.bytes("after"):
                self._write(target, change.bytes("after"))
            self.fault("file:" + change.path)
        require_basis(self.root, proposal, allow_after=True)
        checks = run_checks(self.root)
        self.fault("validated")
        require_basis(self.root, proposal, allow_after=True)
        if self._preflight() != current:
            raise ValueError("preflight changed during live rule validation")
        manifest = load_manifest(self.root)
        rules = rule_bundle_digest(manifest, self.root)
        snapshot = capture_snapshot(
            self.root, self.gate, rule_bundle_paths(manifest, self.root), rules
        )
        draft = preflight_archive_payload(current)
        draft.pop("preflight_receipt_id")
        draft.update({
            "created_at": datetime.now(UTC).isoformat(), "baseline_checks": checks,
            "rule_bundle_digest": rules, "rule_snapshot_id": snapshot,
            "rule_change_proposal_id": proposal.identity,
        })
        identity = calculate_preflight_receipt_id(draft)
        renewed = {**draft, "preflight_receipt_id": identity}
        archive_receipt(self.gate, receipt_id=identity, kind="preflight", payload=renewed)
        require_basis(self.root, proposal, allow_after=True)
        self._json(self.pointer, renewed)
        self.fault("renewed")
        return self._close(proposal, "APPLIED", identity)

    def rollback(self, identity: str, approval: str) -> dict[str, Any]:
        proposal = self._authorize(identity, approval, resume=True)
        if self._preflight()["preflight_receipt_id"] != proposal.preflight_receipt_id:
            raise ValueError("new preflight already published; resume finalization instead")
        for change in proposal.changes:
            require_basis(self.root, proposal, allow_after=True)
            target = safe_metadata(self.root, change.path)
            if target.read_bytes() != change.bytes("before"):
                self._write(target, change.bytes("before"))
            self.fault("rollback:" + change.path)
        require_basis(self.root, proposal, allow_after=False)
        return self._close(proposal, "ROLLED_BACK", None)

    def _close(
        self, proposal: RuleChangeProposal, status: str, new_id: str | None
    ) -> dict[str, Any]:
        result = {
            "proposal_id": proposal.identity, "status": status,
            "old_preflight_receipt_id": proposal.preflight_receipt_id,
            "new_preflight_receipt_id": new_id, "product_accepted": False,
            "closed_at": datetime.now(UTC).isoformat(),
        }
        archive_receipt(
            self.gate, receipt_id=digest(result), kind="rule-change-result", payload=result
        )
        self.fault("sealed")
        self._json(self._used_path(proposal.identity), result)
        self.fault("consumed")
        self.pending.unlink()
        return result
