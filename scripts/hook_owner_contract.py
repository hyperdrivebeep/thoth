"""Host-observed workflow ownership; never infer authority from assistant prose."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

OWNER_OPTION = "--host-owner-receipt"
OWNER_FIELDS = {
    "schema_version", "kind", "session_id", "turn_id", "tool_use_id", "request_digest",
    "observed_at", "owner_receipt_id", "previous_preflight_receipt_id", "nonce",
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value) is None:
        raise ValueError(f"THOTH_OWNER_{field.upper()}_REQUIRED")
    return value


def event_identity(event: dict[str, Any]) -> tuple[str, str]:
    return _identifier(event.get("session_id"), "session_id"), _identifier(event.get("turn_id"), "turn_id")


def validate_owner_context(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != OWNER_FIELDS:
        raise ValueError("THOTH_OWNER_CONTEXT_INVALID")
    if value["schema_version"] != "1.0.0" or value["kind"] != "THOTH_HOST_PREFLIGHT_REQUEST":
        raise ValueError("THOTH_OWNER_CONTEXT_INVALID")
    event_identity(value)
    for field in ("owner_receipt_id", "request_digest"):
        if not isinstance(value[field], str) or re.fullmatch(r"[0-9a-f]{64}", value[field]) is None:
            raise ValueError("THOTH_OWNER_DIGEST_INVALID")
    previous = value["previous_preflight_receipt_id"]
    if previous is not None and (not isinstance(previous, str) or re.fullmatch(r"[0-9a-f]{64}", previous) is None):
        raise ValueError("THOTH_OWNER_PREVIOUS_RECEIPT_INVALID")
    if value["tool_use_id"] is not None and not isinstance(value["tool_use_id"], str):
        raise ValueError("THOTH_OWNER_TOOL_ID_INVALID")
    _identifier(value["nonce"], "nonce")
    if not isinstance(value["observed_at"], str) or datetime.fromisoformat(value["observed_at"]).tzinfo is None:
        raise ValueError("THOTH_OWNER_TIMESTAMP_INVALID")
    if value["owner_receipt_id"] != _digest({k: v for k, v in value.items() if k != "owner_receipt_id"}):
        raise ValueError("THOTH_OWNER_RECEIPT_ID_INVALID")
    return value


def owner_context(preflight: dict[str, Any], gate: Path) -> dict[str, Any] | None:
    if "owner_context" not in preflight:
        return None
    from scripts.architecture_gate_contract import validate_archived_receipt

    owner = validate_owner_context(preflight["owner_context"])
    validate_archived_receipt(gate, receipt_id=owner["owner_receipt_id"], kind="host-session", payload=owner)
    return owner


def require_owner(preflight: dict[str, Any], event: dict[str, Any], gate: Path) -> None:
    session, _turn = event_identity(event)
    owner = owner_context(preflight, gate)
    if owner is None:
        raise ValueError("THOTH_LEGACY_OWNER_UNBOUND_CREATE_NEW_PREFLIGHT")
    if owner["session_id"] != session:
        raise ValueError("THOTH_PREFLIGHT_OWNED_BY_ANOTHER_SESSION")


def workflow_entry(command: str, root: Path) -> str | None:
    """Identify an actual trusted CLI invocation, not a filename inside a read/search string."""
    from scripts.read_only_command_contract import parse_units

    names = {"prepare_architecture_preflight.py", "complete_architecture_gate.py",
             "cancel_architecture_preflight.py", "record_wiki_sync.py", "change_architecture_rules.py"}
    if not any(name in command for name in names):
        return None
    found: list[str] = []
    for unit in parse_units(command, root):
        argv = unit["argv"]
        if len(argv) < 2:
            continue
        executable = argv[0].replace("\\", "/").lower()
        if executable not in {"python", "python.exe", ".venv/scripts/python.exe", "./.venv/scripts/python.exe", (root / ".venv/Scripts/python.exe").as_posix().lower()}:
            continue
        script = argv[1].replace("\\", "/")
        for name in names:
            if script in {"scripts/" + name, (root / "scripts" / name).as_posix()}:
                found.append(name)
    if len(found) > 1:
        raise ValueError("THOTH_OWNER_WORKFLOW_REQUIRES_SINGLE_ENTRY")
    return found[0] if found else None


def _without_owner(arguments: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    seen = False
    while index < len(arguments):
        if arguments[index] == OWNER_OPTION:
            if seen or index + 1 >= len(arguments):
                raise ValueError("THOTH_OWNER_ARGUMENT_INVALID")
            seen = True
            index += 2
        else:
            result.append(arguments[index])
            index += 1
    return result


def _quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@=-]+", value):
        return value
    return "'" + value.replace("'", "''") + "'"


def owned_preflight_input(command: str, event: dict[str, Any], root: Path, gate: Path) -> dict[str, Any]:
    """Rewrite only the already validated literal preflight CLI with an observed receipt."""
    from scripts.architecture_gate_contract import archive_receipt, preflight_archive_payload, validate_archived_receipt, validate_preflight_identity

    session, turn = event_identity(event)
    if any(char in command for char in ";&|<>$`\n\r()"):
        raise ValueError("THOTH_OWNED_PREFLIGHT_REQUIRES_LITERAL_ENTRY")
    argv = shlex.split(command)
    if len(argv) < 3 or Path(argv[1].replace("\\", "/")).name != "prepare_architecture_preflight.py":
        raise ValueError("THOTH_OWNER_PREFLIGHT_ENTRY_INVALID")
    arguments = _without_owner(argv[2:])
    previous_id = None
    current_path = gate / "preflight.json"
    if current_path.is_file():
        previous = json.loads(current_path.read_text(encoding="utf-8"))
        validate_preflight_identity(previous, allowed_statuses=frozenset({"READY_FOR_EDIT", "CANCELLED", "VERIFIED"}))
        previous_id = previous["preflight_receipt_id"]
        validate_archived_receipt(gate, receipt_id=previous_id, kind="preflight", payload=preflight_archive_payload(previous))
        prior_owner = owner_context(previous, gate)
        if previous["status"] == "READY_FOR_EDIT" and prior_owner is not None and prior_owner["session_id"] != session:
            raise ValueError("THOTH_ACTIVE_OWNER_MUST_RELEASE_PREFLIGHT_BEFORE_HANDOFF")
    body: dict[str, Any] = {
        "schema_version": "1.0.0", "kind": "THOTH_HOST_PREFLIGHT_REQUEST",
        "session_id": session, "turn_id": turn, "tool_use_id": event.get("tool_use_id"),
        "request_digest": _digest(arguments), "observed_at": datetime.now(UTC).isoformat(),
        "previous_preflight_receipt_id": previous_id, "nonce": str(uuid4()),
    }
    proof = {**body, "owner_receipt_id": _digest(body)}
    validate_owner_context(proof)
    archive_receipt(gate, receipt_id=proof["owner_receipt_id"], kind="host-session", payload=proof)
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ValueError("THOTH_OWNER_TOOL_INPUT_INVALID")
    key = "cmd" if "cmd" in tool_input else "command"
    return {**tool_input, key: " ".join(_quote(a) for a in (*argv[:2], *arguments, OWNER_OPTION, proof["owner_receipt_id"]))}


def load_requested_owner(gate: Path, receipt_id: str | None, arguments: list[str], previous: dict[str, Any] | None) -> dict[str, Any] | None:
    if receipt_id is None:
        return None  # Standalone legacy CLI remains readable; host protected writes require binding.
    if re.fullmatch(r"[0-9a-f]{64}", receipt_id) is None:
        raise ValueError("THOTH_OWNER_DIGEST_INVALID")
    proof = validate_owner_context(json.loads((gate / "receipts" / f"{receipt_id}.host-session.json").read_text(encoding="utf-8")))
    if proof["request_digest"] != _digest(_without_owner(arguments)):
        raise ValueError("THOTH_OWNER_REQUEST_DIFFERS")
    if proof["previous_preflight_receipt_id"] != (None if previous is None else previous["preflight_receipt_id"]):
        raise ValueError("THOTH_OWNER_PREVIOUS_PREFLIGHT_CHANGED")
    if (gate / "owner-consumption" / f"{receipt_id}.json").exists():
        raise ValueError("THOTH_OWNER_REQUEST_ALREADY_CONSUMED")
    return proof


def consume_owner(gate: Path, owner: dict[str, Any], preflight_id: str) -> None:
    directory = gate / "owner-consumption"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / f"{owner['owner_receipt_id']}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"owner_receipt_id": owner["owner_receipt_id"], "preflight_receipt_id": preflight_id}, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
