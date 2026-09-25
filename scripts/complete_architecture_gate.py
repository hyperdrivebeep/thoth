from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import version
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import (
    ROOT,
    known_exception_ids,
    load_manifest,
    rule_bundle_digest,
)
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_verification_receipt_id,
    preflight_archive_payload,
    scope_digest,
    validate_archived_receipt,
    validate_preflight,
    validate_wiki_sync,
)
from scripts.verification_bundle_contract import (
    build_bundle,
    current_index,
    portable_store_digest,
    publish_bundle,
)
from scripts.verification_identity import SOURCE_POLICY, assert_unchanged, capture_source_manifest
from scripts.verification_progress import require_observations, start_run, update_run, utc_now
from scripts.verification_profile_contract import select_current
from scripts.verification_profiles import canonical_digest

GATE_DIR = Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth" / "architecture"))
PREFLIGHT = GATE_DIR / "preflight.json"
VERIFICATION = GATE_DIR / "verification.json"


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Broaden verification to FULL")
    args = parser.parse_args([] if argv is None else argv)
    if (GATE_DIR / "pending-rule-restore.json").exists():
        raise ValueError("pending rule restoration is not completion evidence")
    if (GATE_DIR / "pending-rule-change.json").exists():
        raise ValueError("pending metadata transaction is not completion evidence")
    if not PREFLIGHT.is_file():
        raise FileNotFoundError("architecture preflight is required")
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    scopes = validate_preflight(
        preflight,
        allowed_statuses=frozenset({"READY_FOR_EDIT"}),
    )
    validate_archived_receipt(
        GATE_DIR,
        receipt_id=str(preflight["preflight_receipt_id"]),
        kind="preflight",
        payload=preflight_archive_payload(preflight),
    )
    from scripts.hook_owner_contract import owner_context

    owner_context(preflight, GATE_DIR)
    wiki_sync_path = GATE_DIR / "wiki-sync" / f"{preflight['preflight_receipt_id']}.json"
    if not wiki_sync_path.is_file():
        raise FileNotFoundError("wiki-sync receipt is required before completion")
    wiki_sync = json.loads(wiki_sync_path.read_text(encoding="utf-8"))
    validate_wiki_sync(wiki_sync, preflight=preflight)
    validate_archived_receipt(
        GATE_DIR,
        receipt_id=str(wiki_sync["wiki_sync_receipt_id"]),
        kind="wiki-sync",
        payload=wiki_sync,
    )
    manifest = load_manifest()
    if preflight["rule_bundle_digest"] != rule_bundle_digest(manifest):
        raise ValueError("rule bundle changed after preflight; create a new preflight")
    remaining = set(preflight["planned_remediations"]) & known_exception_ids(manifest)
    if remaining:
        raise ValueError(f"planned ratchet exceptions were not removed: {sorted(remaining)}")

    source_manifest = capture_source_manifest(ROOT)
    before_source = str(source_manifest["repository_digest"])
    before_index = str(source_manifest["index_digest"])
    before_scope = scope_digest(list(scopes))
    before_portable = portable_store_digest(ROOT)
    previous = current_index(ROOT)
    expected_index = None if previous is None else previous["index_id"]
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if shell is None:
        raise ValueError("PowerShell is required for verification")
    if any(os.environ.get(key) for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")):
        raise ValueError("external pytest options/plugins are not a sealed verification selection")
    runtime = {
        "python_version": platform.python_version(), "platform": sys.platform,
        "shell": Path(shell).name, "pytest_version": version("pytest"),
        "python_implementation": platform.python_implementation(),
        "pytest_autoload": "disabled" if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") else "enabled",
    }
    selection, baseline = select_current(
        ROOT, source_manifest, tuple(map(str, manifest["rule_documents"])),
        force_full=args.full or preflight["acceptance_id"] != "A11",
        runtime=runtime,
    )
    profile = selection["profile"]
    task = "verify" if profile == "FULL" else "tooling"
    started_at = datetime.now(UTC).isoformat()

    run_directory, run = start_run(ROOT, kind=profile + "_VERIFICATION", preflight=preflight, source=source_manifest)
    update_run(run_directory, run, verification_profile=profile, selection=selection, full_suite_verified=False)
    print(f"THOTH_VERIFICATION_RUN {run['run_id']}", flush=True)
    print(f"THOTH_VERIFICATION_PROFILE {profile} {','.join(selection['reason_codes'])}", flush=True)
    try:
        completed = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "Makefile.ps1"), task],
            cwd=ROOT, check=False, env={**os.environ, "THOTH_VERIFICATION_RUN_ID": run["run_id"]},
        )
    except KeyboardInterrupt:
        update_run(run_directory, run, state="INTERRUPTED", finished_at=utc_now())
        raise
    except OSError:
        update_run(run_directory, run, state="PROCESS_START_FAILED", finished_at=utc_now())
        raise
    if completed.returncode != 0:
        update_run(run_directory, run, state="VERIFY_FAILED", verify_exit_code=completed.returncode, finished_at=utc_now())
        raise RuntimeError(f"{profile} verification failed with exit code {completed.returncode}")
    update_run(run_directory, run, state="VERIFY_FINISHED_NOT_SEALED", verify_exit_code=0, finished_at=utc_now())
    finished_at = datetime.now(UTC).isoformat()
    assert_unchanged(ROOT, before_source, before_index)
    if capture_source_manifest(ROOT) != source_manifest:
        raise ValueError("tested source manifest or HEAD changed during verification")
    if portable_store_digest(ROOT) != before_portable:
        raise ValueError("portable evidence changed during verification")
    if before_scope != scope_digest(list(scopes)):
        raise ValueError("declared scope changed during verification")
    validate_wiki_sync(wiki_sync, preflight=preflight)
    if json.loads(PREFLIGHT.read_text(encoding="utf-8")) != preflight:
        raise ValueError("active preflight changed during verification")
    if json.loads(wiki_sync_path.read_text(encoding="utf-8")) != wiki_sync:
        raise ValueError("active wiki-sync pointer changed during verification")
    observations = require_observations(
        run_directory, run["run_id"], selection=selection, source=source_manifest, baseline=baseline,
    )

    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": preflight["acceptance_id"],
        "rule_bundle_digest": preflight["rule_bundle_digest"],
        "scope_digest": before_scope,
        "repository_digest": before_source,
        "index_digest": before_index,
        "source_manifest_digest": source_manifest["manifest_digest"],
        "source_manifest_policy": SOURCE_POLICY,
        "verification_profile": profile,
        "full_suite_verified": profile == "FULL",
        "selection_digest": canonical_digest(selection),
        "wiki_sync_receipt_id": wiki_sync["wiki_sync_receipt_id"],
        "status": "PASS",
        "verified_at": datetime.now(UTC).isoformat(),
    }
    verification_id = calculate_verification_receipt_id(draft)
    payload = {**draft, "verification_receipt_id": verification_id}
    archive_receipt(
        GATE_DIR,
        receipt_id=str(preflight["preflight_receipt_id"]),
        kind="preflight",
        payload=preflight,
    )
    archive_receipt(
        GATE_DIR,
        receipt_id=verification_id,
        kind="verification",
        payload=payload,
    )
    bundle = build_bundle(
        preflight=preflight,
        verification=payload,
        wiki=wiki_sync,
        source_manifest=source_manifest,
        run_evidence={
            "kind": profile + "_VERIFY_SUBPROCESS",
            "run_id": run["run_id"],
            "command": ["Makefile.ps1", task],
            "selection": selection,
            "observations": observations,
            "exit_code": completed.returncode,
            "started_at": started_at,
            "finished_at": finished_at,
            "runtime": runtime,
        },
    )
    publish_bundle(ROOT, bundle, expected_index_id=expected_index)
    _atomic_write(VERIFICATION, payload)
    preflight.update(
        {
            "status": "VERIFIED",
            "verification_receipt_id": verification_id,
            "verified_at": payload["verified_at"],
        }
    )
    _atomic_write(PREFLIGHT, preflight)
    update_run(run_directory, run, state="SEALED" if profile == "FULL" else "TOOLING_SEALED",
               full_suite_verified=profile == "FULL", bundle_id=bundle["bundle_id"], verification_receipt_id=verification_id)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
