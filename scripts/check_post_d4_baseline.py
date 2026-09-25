from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import cast

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_contract import load_manifest, rule_bundle_digest  # noqa: E402

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS  # noqa: E402
from thoth.protocol.registry import PUBLIC_METHODS  # noqa: E402

BASELINE = ROOT / "config" / "post-d4-baseline.json"
NOTIFICATION_HISTORY = ROOT / "config" / "post-d4-notifications-historical.json"
PACK_ROOT = ROOT / "examples" / "projectpacks"
_HISTORICAL_NOTIFICATION_COUNT = 219
_HISTORICAL_NAMES_SHA256 = "03b79293bc2513d9f9f234ec0d50ec2372dcc4d8bd58838d3891afdcbe1aecca"
_HISTORICAL_COMMIT = "b334ef699ffef69507c2d790e658a1cc8fdf2daf"
_HISTORICAL_SOURCE_SHA256 = "c79aee5d9acc6412b891bb2d979ac6c6d9b83921d044214047ee4143951176ac"


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _receipt_digest(payload: dict[str, object]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "baseline_receipt_digest"}
    return hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _historical_notification_names(errors: list[str]) -> frozenset[str] | None:
    try:
        loaded: object = json.loads(NOTIFICATION_HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        errors.append("historical notification snapshot unavailable")
        return None
    if not isinstance(loaded, dict):
        errors.append("historical notification snapshot invalid")
        return None
    snapshot = cast(dict[str, object], loaded)
    raw_names = snapshot.get("names")
    if not isinstance(raw_names, list):
        errors.append("historical notification snapshot names invalid")
        return None
    names = cast(list[object], raw_names)
    if any(not isinstance(name, str) for name in names):
        errors.append("historical notification snapshot names invalid")
        return None
    sorted_names = cast(list[str], names)
    digest = hashlib.sha256(
        json.dumps(sorted_names, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        snapshot.get("schema_version") != "1.0.0"
        or snapshot.get("record_type") != "POST_D4_NOTIFICATION_NAMES"
        or snapshot.get("baseline_creation_commit") != _HISTORICAL_COMMIT
        or snapshot.get("historical_source_path") != "src/thoth/protocol/notifications.py"
        or snapshot.get("historical_source_sha256") != _HISTORICAL_SOURCE_SHA256
        or snapshot.get("implemented_notification_count") != _HISTORICAL_NOTIFICATION_COUNT
        or snapshot.get("sorted_names_sha256") != _HISTORICAL_NAMES_SHA256
        or len(sorted_names) != _HISTORICAL_NOTIFICATION_COUNT
        or len(set(sorted_names)) != len(sorted_names)
        or sorted_names != sorted(sorted_names)
        or digest != _HISTORICAL_NAMES_SHA256
    ):
        errors.append("historical notification snapshot mismatch")
        return None
    return frozenset(sorted_names)


def main() -> int:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    errors: list[str] = []
    additive_drift: list[str] = []
    manifest = load_manifest()
    architecture = payload["architecture"]
    if architecture["rule_bundle_digest"] != rule_bundle_digest(manifest):
        additive_drift.append("rule bundle digest advanced after P0")
    if architecture["exception_count"] != len(manifest["known_exceptions"]):
        errors.append("architecture exception count drift")
    blockers = sum(
        bool(item.get("blocks_acceptance")) for item in manifest["known_exceptions"]
    )
    if architecture["acceptance_blocker_count"] != blockers:
        errors.append("Acceptance blocker count drift")

    protocol = payload["protocol"]
    if len(PUBLIC_METHODS) < protocol["runtime_public_method_count"]:
        errors.append("runtime public method count fell below P0")
    elif protocol["runtime_public_method_count"] != len(PUBLIC_METHODS):
        additive_drift.append(
            "runtime public methods advanced after P0; current exact parity is checked separately"
        )
    historical_names = _historical_notification_names(errors)
    if historical_names is not None:
        if protocol["implemented_notification_count"] != len(historical_names):
            errors.append("historical implemented notification count mismatch")
        missing = historical_names - IMPLEMENTED_NOTIFICATIONS
        if missing:
            errors.append(f"historical implemented notification names lost: {len(missing)}")
        else:
            added = IMPLEMENTED_NOTIFICATIONS - historical_names
            if added:
                additive_drift.append(
                    f"implemented notifications advanced after P0: {len(added)} additions"
                )

    config = Config(str(ROOT / "alembic.ini"))
    heads = tuple(ScriptDirectory.from_config(config).get_heads())
    migration_count = len(tuple((ROOT / "migrations" / "versions").glob("*.py")))
    if heads != (payload["migration"]["head"],):
        additive_drift.append("migration head advanced after P0")
    if migration_count != payload["migration"]["migration_count"]:
        additive_drift.append("migration count advanced after P0")

    for name, expected in payload["projectpack_digests"].items():
        if _tree_digest(PACK_ROOT / name) != expected:
            additive_drift.append(f"ProjectPack digest advanced after P0: {name}")
    if any(value != "NOT_RUN" for value in payload["external_gates"].values()):
        errors.append("external gate status drift")
    actual_receipt = _receipt_digest(payload)
    if payload["baseline_receipt_digest"] != actual_receipt:
        errors.append("baseline receipt digest mismatch")
    result = {
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
        "baseline_receipt_digest": actual_receipt,
        "record_type": payload["record_type"],
        "additive_drift": additive_drift,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
