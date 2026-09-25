"""Read portable verification state or import an explicitly historical receipt bundle."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verification_bundle_contract import (  # noqa: E402
    build_bundle,
    current_index,
    portable_store_digest,
    publish_bundle,
    read_bundle,
)
from scripts.verification_identity import git_bytes, source_matches  # noqa: E402
from scripts.verification_profile_contract import receipt_profile  # noqa: E402


def verification_status(root: Path) -> dict[str, Any]:
    portable_store_digest(root)
    current = current_index(root)
    if current is None:
        return {"status": "NO_PORTABLE_COMPLETION", "current_source_verified": False}
    bundle = read_bundle(root, current["bundle_id"])
    manifest = bundle["source_manifest"]
    matching = source_matches(root, manifest)
    profile = receipt_profile(bundle["verification"])
    active_path = root / ".thoth/architecture/preflight.json"
    active = json.loads(active_path.read_text(encoding="utf-8")) if active_path.is_file() else None
    head = git_bytes(root, "rev-parse", "HEAD").decode().strip()
    return {
        "status": "COMPLETED_RECORD" if profile == "FULL" else "TOOLING_COMPLETED_RECORD",
        "verification_profile": profile,
        "full_suite_verified": profile == "FULL",
        "current_scope_verified": matching,
        "index_id": current["index_id"],
        "bundle_id": current["bundle_id"],
        "verification_receipt_id": current["verification_receipt_id"],
        "plan_id": bundle["preflight"].get("plan_id"),
        "node_id": bundle["preflight"].get("node_id"),
        "acceptance_id": bundle["verification"]["acceptance_id"],
        "verified_at": bundle["verification"]["verified_at"],
        "tested_head": manifest["head"],
        "current_head": head,
        "same_head": head == manifest["head"],
        "current_source_verified": matching and profile == "FULL",
        "active_local_gate": None if active is None else active.get("status"),
        "active_preflight_matches": active is not None
        and active.get("preflight_receipt_id") == bundle["preflight"]["preflight_receipt_id"],
        "evidence_limit": (
            "Preserved local workflow result; not an independent rerun, product maturity, "
            "or proof that all plan nodes are complete."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("current", "import-historical", "running"))
    parser.add_argument("--run-id")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--samples", type=int, default=0)
    parser.add_argument("--long-after", type=float, default=60.0)
    for name in ("preflight", "verification", "wiki", "manifest"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.action == "running":
        from scripts.verification_progress import running_status

        if not args.run_id or not 0.05 <= args.interval <= 60 or args.samples < 0 or args.long_after < 0:
            parser.error("running requires --run-id and bounded positive observation options")
        previous = None
        sample = 0
        try:
            while True:
                value = running_status(ROOT, args.run_id, long_after=args.long_after)
                signature = json.dumps({k: v for k, v in value.items() if k not in {"test_elapsed_s", "stage_elapsed_s", "last_event_age_s"}}, sort_keys=True)
                if signature != previous:
                    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)
                    previous = signature
                sample += 1
                if not args.watch or value.get("terminal") or (args.samples and sample >= args.samples):
                    return 0
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0  # Ending this read-only watch never interrupts the observed process.
    if args.watch or args.run_id:
        parser.error("watch/run-id are only valid for running")
    try:
        if args.action == "current":
            value = verification_status(ROOT)
        else:
            files = (args.preflight, args.verification, args.wiki, args.manifest)
            if any(path is None for path in files):
                raise ValueError("HISTORICAL_IMPORT_REQUIRES_ALL_FOUR_FILES")
            preflight, verification, wiki, manifest = (
                json.loads(path.read_text(encoding="utf-8")) for path in files
            )
            portable_store_digest(ROOT)
            current = current_index(ROOT)
            bundle = build_bundle(
                preflight=preflight,
                verification=verification,
                wiki=wiki,
                source_manifest=manifest,
                historical=True,
            )
            publish_bundle(
                ROOT,
                bundle,
                expected_index_id=None if current is None else current["index_id"],
                historical_only=True,
            )
            value = {
                "status": "HISTORICAL_IMPORTED",
                "bundle_id": bundle["bundle_id"],
                "current_index_changed": False,
            }
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps({"status": "INVALID_OR_UNAVAILABLE", "reason": str(exc)}, ensure_ascii=False)
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
