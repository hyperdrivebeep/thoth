"""Typed, content-bound proposals for the deliberately small metadata repair lane."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.architecture_gate_contract import archive_receipt, validate_archived_receipt
from scripts.verification_identity import capture_source_manifest, validate_source_manifest

METADATA_PATHS = frozenset({
    "config/architecture-conformance.json", "config/module-responsibility-budget.json"
})


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def checked_id(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("invalid transaction identity")
    return value


@dataclass(frozen=True)
class RuleFileChange:
    path: str
    before: str
    after: str

    def bytes(self, which: str) -> bytes:
        return base64.b64decode(getattr(self, which), validate=True)


@dataclass(frozen=True)
class RuleChangeProposal:
    preflight_receipt_id: str
    rule_bundle_digest: str
    basis: dict[str, Any]
    changes: tuple[RuleFileChange, ...]
    checks: list[dict[str, object]]
    schema_version: str = "1.0.0"
    approved_scope: tuple[str, ...] = ()

    @property
    def identity(self) -> str:
        return digest(asdict(self))


def safe_metadata(root: Path, relative: str) -> Path:
    path = root / relative
    if relative not in METADATA_PATHS or path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError("metadata repair path is not an exact allowed regular file")
    return path


def validate_metadata_change(relative: str, before: bytes, after: bytes) -> None:
    if relative not in METADATA_PATHS or len(after) > 2_000_000:
        raise ValueError("metadata repair cannot change this file")
    old, new = json.loads(before), json.loads(after)
    if relative.endswith("architecture-conformance.json"):
        old_extensions = old.pop("extension_points")
        new_extensions = new.pop("extension_points")
        if old != new or len(old_extensions) != len(new_extensions):
            raise ValueError("metadata repair cannot change owners, authority or rule definitions")
        for prior, candidate in zip(old_extensions, new_extensions, strict=True):
            previous_targets = prior.pop("consumer_targets", None)
            next_targets = candidate.pop("consumer_targets", None)
            if (previous_targets is not None or next_targets is not None) and (
                    not isinstance(previous_targets, list) or not isinstance(next_targets, list)
                    or len(previous_targets) != len(next_targets) or not next_targets
                    or any(not isinstance(target, str) or not target for target in next_targets)
                    or len(next_targets) != len(set(next_targets))
            ):
                raise ValueError("consumer declarations cannot be removed or expanded")
            if prior != candidate:
                raise ValueError("only existing extension consumer_targets may be corrected")
    else:
        old_functions = old.pop("watched_long_functions")
        new_functions = new.pop("watched_long_functions")
        old_modules, new_modules = old.pop("watched_modules"), new.pop("watched_modules")
        if (
            old != new or not set(new_functions) <= set(old_functions)
            or set(old_modules) != set(new_modules)
        ):
            raise ValueError("metadata repair cannot change limits, roots or add watches")
        for name, size in new_functions.items():
            if type(size) is not int or size > old_functions[name]:
                raise ValueError("function ratchet cannot grow")
        for name, candidate in new_modules.items():
            prior = old_modules[name]
            old_size, new_size = prior.pop("baseline_lines"), candidate.pop("baseline_lines")
            if prior != candidate or type(new_size) is not int or new_size > old_size:
                raise ValueError("only a shrinking module baseline may be corrected")


def archive_proposal(gate: Path, proposal: RuleChangeProposal) -> str:
    archive_receipt(
        gate, receipt_id=proposal.identity, kind="rule-proposal", payload=asdict(proposal)
    )
    return proposal.identity


def load_proposal(gate: Path, identity: str) -> RuleChangeProposal:
    from scripts.required_architecture_checks import validate_check_results

    checked_id(identity)
    value = json.loads((gate / "receipts" / f"{identity}.rule-proposal.json").read_bytes())
    fields = {
        "schema_version", "preflight_receipt_id", "rule_bundle_digest", "basis", "changes",
        "checks", "approved_scope",
    }
    if set(value) != fields or digest(value) != identity or value["schema_version"] != "1.0.0":
        raise ValueError("proposal identity or schema mismatch")
    checked_id(value["preflight_receipt_id"])
    checked_id(value["rule_bundle_digest"])
    scopes = value["approved_scope"]
    if not isinstance(scopes, list) or not scopes or any(not isinstance(s, str) for s in scopes):
        raise ValueError("invalid proposal approval scope")
    validate_source_manifest(value["basis"])
    validate_check_results(value["checks"])
    changes = tuple(RuleFileChange(**entry) for entry in value["changes"])
    if not changes or len(changes) > 2 or len({change.path for change in changes}) != len(changes):
        raise ValueError("invalid metadata change set")
    for change in changes:
        validate_metadata_change(change.path, change.bytes("before"), change.bytes("after"))
    return RuleChangeProposal(
        value["preflight_receipt_id"], value["rule_bundle_digest"], value["basis"],
        changes, value["checks"], approved_scope=tuple(scopes)
    )


def field_diff(before: Any, after: Any, prefix: str = "$") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            result.extend(field_diff(before.get(key), after.get(key), prefix + "." + key))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        result = []
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            result.extend(field_diff(old, new, f"{prefix}[{index}]"))
        return result
    return [{"field": prefix, "before": before, "after": after}]


def proposal_preview(proposal: RuleChangeProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.identity, "status": "STAGED_NOT_APPLIED",
        "approved_scope": proposal.approved_scope,
        "head": proposal.basis["head"], "index_digest": proposal.basis["index_digest"],
        "source_manifest_digest": proposal.basis["manifest_digest"],
        "changes": [{
            "path": change.path,
            "before_sha256": hashlib.sha256(change.bytes("before")).hexdigest(),
            "after_sha256": hashlib.sha256(change.bytes("after")).hexdigest(),
            "field_diff": field_diff(
                json.loads(change.bytes("before")), json.loads(change.bytes("after"))
            ),
        } for change in proposal.changes],
    }


def require_publication(gate: Path, preflight: dict[str, Any]) -> None:
    identity = preflight.get("rule_change_proposal_id")
    if identity is None:
        return
    proposal = load_proposal(gate, checked_id(identity))
    result = json.loads((gate / "rule-transactions" / f"{identity}.json").read_bytes())
    if (
        result.get("status") != "APPLIED" or result.get("proposal_id") != identity
        or result.get("old_preflight_receipt_id") != proposal.preflight_receipt_id
        or result.get("new_preflight_receipt_id") != preflight.get("preflight_receipt_id")
        or tuple(preflight["declared_scope"]) != proposal.approved_scope
    ):
        raise ValueError("metadata transaction publication does not match preflight")
    validate_archived_receipt(
        gate, receipt_id=digest(result), kind="rule-change-result", payload=result
    )


def require_basis(root: Path, proposal: RuleChangeProposal, *, allow_after: bool) -> None:
    current = capture_source_manifest(root)
    for key in ("head", "index_digest", "policy"):
        if current[key] != proposal.basis[key]:
            raise ValueError(f"transaction basis changed: {key}")
    overrides = {change.path: change for change in proposal.changes}
    entries = {entry["path"]: entry for entry in current["files"]}
    expected = {entry["path"]: entry for entry in proposal.basis["files"]}
    if entries.keys() != expected.keys():
        raise ValueError("transaction source inventory changed")
    for path, entry in entries.items():
        change = overrides.get(path)
        if change is None:
            if entry != expected[path]:
                raise ValueError(f"unrelated source changed: {path}")
            continue
        actual = safe_metadata(root, path).read_bytes()
        permitted = {change.bytes("before")}
        if allow_after:
            permitted.add(change.bytes("after"))
        if actual not in permitted:
            raise ValueError(f"unknown metadata drift: {path}")
