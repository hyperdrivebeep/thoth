"""Bindings between profile decisions, prior FULL proof and actual test observations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scripts.verification_profiles import canonical_digest, select_manifest_profile

PROFILE_FIELDS = frozenset({"verification_profile", "full_suite_verified", "selection_digest"})


def receipt_profile(verification: dict[str, Any]) -> str:
    present = PROFILE_FIELDS.intersection(verification)
    if not present:
        return "FULL"  # Legacy identities are unchanged; their FULL run is checked by the bundle.
    if present != PROFILE_FIELDS:
        raise ValueError("PROFILE_RECEIPT_FIELDS_INCOMPLETE")
    profile = verification["verification_profile"]
    if profile not in {"FULL", "TOOLING"} or verification["full_suite_verified"] is not (profile == "FULL"):
        raise ValueError("PROFILE_RECEIPT_CLAIM_INVALID")
    if not isinstance(verification["selection_digest"], str) or re.fullmatch(
        r"[0-9a-f]{64}", verification["selection_digest"]
    ) is None:
        raise ValueError("PROFILE_SELECTION_DIGEST_INVALID")
    return profile


def last_full_baseline(root: Path) -> dict[str, Any] | None:
    from scripts.verification_bundle_contract import current_index, read_bundle
    index = current_index(root)
    if index is None:
        return None
    bundle = read_bundle(root, index["bundle_id"])
    if receipt_profile(bundle["verification"]) == "TOOLING":
        bundle = read_bundle(root, bundle["run_evidence"]["selection"]["baseline_bundle_id"])
    if bundle["proof_scope"] != "LOCAL_WORKFLOW_VERIFICATION" or receipt_profile(bundle["verification"]) != "FULL":
        raise ValueError("PROFILE_BASELINE_IS_NOT_FULL")
    return bundle


def select_current(
    root: Path, source: dict[str, Any], rule_documents: tuple[str, ...], *, force_full: bool = False,
    runtime: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    baseline = last_full_baseline(root)
    baseline_policy = None if baseline is None else (baseline.get("run_evidence") or {}).get(
        "selection", {}
    ).get("policy")
    selection = select_manifest_profile(
        source, baseline=None if baseline is None else baseline["source_manifest"],
        baseline_id=None if baseline is None else baseline["bundle_id"],
        baseline_policy=baseline_policy, rule_documents=rule_documents, force_full=force_full,
        runtime=runtime, baseline_runtime=None if baseline is None else baseline["run_evidence"].get("runtime"),
    )
    return selection, baseline


def _collection(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("PROFILE_COLLECTION_EMPTY")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"case_id", "file", "function"}:
            raise ValueError("PROFILE_COLLECTION_ENTRY_INVALID")
        if not isinstance(item["case_id"], str) or re.fullmatch(r"[0-9a-f]{20}", item["case_id"]) is None:
            raise ValueError("PROFILE_COLLECTION_ID_INVALID")
        if item["case_id"] in seen:
            raise ValueError("PROFILE_COLLECTION_DUPLICATE")
        seen.add(item["case_id"])
        path = item["file"]
        if not isinstance(path, str) or not path.startswith("tests/") or Path(path).is_absolute() or any(
            c in path for c in "[]\\\n\r\x1b"
        ) or ".." in Path(path).parts:
            raise ValueError("PROFILE_COLLECTION_PATH_INVALID")
        if not isinstance(item["function"], str) or not item["function"].isidentifier():
            raise ValueError("PROFILE_COLLECTION_FUNCTION_INVALID")
    return value


def validate_observation_proof(
    observations: dict[str, Any], selection: dict[str, Any], source: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    *, check_baseline: bool = True,
) -> None:
    if set(observations) != {"stages", "collection", "results", "pytest_exit_code"}:
        raise ValueError("PROFILE_OBSERVATION_SCHEMA_INVALID")
    if type(observations["pytest_exit_code"]) is not int or observations["pytest_exit_code"] != 0:
        raise ValueError("PROFILE_TESTS_DID_NOT_PASS")
    stages = observations["stages"]
    expected = selection["expected_stages"]
    if not isinstance(stages, list) or not all(isinstance(s, dict) for s in stages) or [s.get("name") for s in stages] != expected or any(
        s.get("state") != "FINISHED" or type(s.get("exit_code")) is not int or s["exit_code"] != 0
        for s in stages
    ):
        raise ValueError("PROFILE_MANDATORY_STAGES_MISSING")
    collection = _collection(observations["collection"])
    by_id = {case["case_id"]: case for case in collection}
    results = observations["results"]
    if not isinstance(results, list) or len(results) != len(collection):
        raise ValueError("PROFILE_UNFINISHED_COLLECTION")
    observed_ids = []
    for result in results:
        if not isinstance(result, dict) or set(result) != {"case_id", "outcome"}:
            raise ValueError("PROFILE_RESULT_INVALID")
        if result["outcome"] not in {"passed", "skipped", "xfailed", "xpassed"}:
            raise ValueError("PROFILE_UNSUCCESSFUL_CASE")
        observed_ids.append(result["case_id"])
    if len(set(observed_ids)) != len(observed_ids) or set(observed_ids) != set(by_id):
        raise ValueError("PROFILE_COLLECTION_RESULT_DIFFERS")
    files = {item["file"] for item in collection}
    mandatory_files = {
        item["path"] for item in source["files"]
        if re.fullmatch(r"tests/architecture/test_[^/]+\.py", item["path"]) and item["sha256"]
    }
    if not mandatory_files or not mandatory_files <= files:
        raise ValueError("PROFILE_MANDATORY_TEST_FILE_MISSING")
    if selection["profile"] == "TOOLING":
        if any(result["outcome"] != "passed" for result in results):
            raise ValueError("PROFILE_MANDATORY_CASE_NOT_PASSED")
        if any(not c["file"].startswith("tests/architecture/") for c in collection):
            raise ValueError("PROFILE_TOOLING_SELECTION_DIFFERS")
        if not check_baseline:
            return  # Cross-record checks still run before publication/read of stored proof.
        if baseline is None or not (baseline.get("run_evidence") or {}).get("observations"):
            raise ValueError("PROFILE_BASELINE_COLLECTION_UNAVAILABLE")
        required = {
            c["case_id"] for c in baseline["run_evidence"]["observations"]["collection"]
            if c["file"].startswith("tests/architecture/")
        }
        if not required or not required <= set(by_id):
            raise ValueError("PROFILE_MANDATORY_BASELINE_CASE_MISSING")


def validate_profile_run(value: dict[str, Any]) -> None:
    """Local shape/binding checks; stored baseline is checked by read_bundle/publication."""
    verification, run = value["verification"], value["run_evidence"]
    profile = receipt_profile(verification)
    selection = run["selection"]
    if canonical_digest(selection) != verification["selection_digest"]:
        raise ValueError("PROFILE_SELECTION_BINDING_DIFFERS")
    if selection["profile"] != profile or selection["requires_full_suite"] is not (profile == "FULL"):
        raise ValueError("PROFILE_RUN_CLAIM_DIFFERS")
    expected_kind = "FULL_VERIFY_SUBPROCESS" if profile == "FULL" else "TOOLING_VERIFY_SUBPROCESS"
    expected_command = ["Makefile.ps1", "verify" if profile == "FULL" else "tooling"]
    expected_scope = "LOCAL_WORKFLOW_VERIFICATION" if profile == "FULL" else "LOCAL_TOOLING_VERIFICATION"
    if run["kind"] != expected_kind or run["command"] != expected_command or value["proof_scope"] != expected_scope:
        raise ValueError("PROFILE_RUN_ROUTE_DIFFERS")
    # Basic observations cannot masquerade as complete even before their baseline is loaded.
    if selection["current_source_digest"] != value["source_manifest"]["repository_digest"]:
        raise ValueError("PROFILE_SOURCE_BINDING_DIFFERS")
    if not isinstance(selection.get("force_full"), bool):
        raise ValueError("PROFILE_FORCE_FULL_INVALID")
    if selection.get("runtime") != run["runtime"]:
        raise ValueError("PROFILE_RUNTIME_BINDING_DIFFERS")
    if profile == "TOOLING" and value["preflight"]["acceptance_id"] != "A11":
        raise ValueError("PROFILE_TOOLING_REQUIRES_A11")
    validate_observation_proof(run["observations"], selection, value["source_manifest"], check_baseline=False)


def validate_profile_baseline(root: Path, bundle: dict[str, Any], depth: int = 0) -> None:
    from scripts.verification_bundle_contract import read_bundle
    if "selection" not in (bundle.get("run_evidence") or {}):
        return
    if depth > 128:
        raise ValueError("PROFILE_BASELINE_CHAIN_BUDGET")
    selection = bundle["run_evidence"]["selection"]
    identifier = selection["baseline_bundle_id"]
    baseline = None if identifier is None else read_bundle(root, identifier, _profile_depth=depth + 1)
    if baseline is not None and (
        baseline["proof_scope"] != "LOCAL_WORKFLOW_VERIFICATION"
        or receipt_profile(baseline["verification"]) != "FULL"
    ):
        raise ValueError("PROFILE_BASELINE_IS_NOT_FULL")
    policy = None if baseline is None else (baseline.get("run_evidence") or {}).get("selection", {}).get("policy")
    rules = tuple(selection["rule_documents"])
    if selection["profile"] == "TOOLING" and baseline is not None:
        # Independent floor: baseline data, not the candidate selector's decision, owns these pins.
        before = {f["path"]: f["sha256"] for f in baseline["source_manifest"]["files"]}
        after = {f["path"]: f["sha256"] for f in bundle["source_manifest"]["files"]}
        if not isinstance(policy, dict) or selection["policy"] != policy or any(
            not before.get(path) or before.get(path) != after.get(path)
            for path in policy["control_paths"]
        ):
            raise ValueError("PROFILE_PRIOR_POLICY_REQUIRES_FULL")
        if selection["runtime"] != baseline["run_evidence"]["runtime"]:
            raise ValueError("PROFILE_PRIOR_RUNTIME_REQUIRES_FULL")
        rules = tuple(baseline["run_evidence"]["selection"]["rule_documents"])
    expected = select_manifest_profile(
        bundle["source_manifest"], baseline=None if baseline is None else baseline["source_manifest"],
        baseline_id=identifier, baseline_policy=policy, rule_documents=rules,
        force_full=selection["force_full"],
        runtime=selection["runtime"], baseline_runtime=None if baseline is None else baseline["run_evidence"].get("runtime"),
    )
    if expected != selection:
        raise ValueError("PROFILE_ACTUAL_DIFF_SELECTION_DIFFERS")
    validate_observation_proof(
        bundle["run_evidence"]["observations"], selection, bundle["source_manifest"], baseline,
    )


def documentation_only_delta(
    root: Path, verification: dict[str, Any], gate: Path | None = None,
) -> bool:
    """Release a completed owner for ordinary document review; issue no new verification."""
    from scripts.architecture_contract import load_manifest
    from scripts.architecture_gate_contract import validate_archived_receipt
    from scripts.verification_bundle_contract import current_index, portable_store_digest, read_bundle
    from scripts.verification_identity import capture_source_manifest
    from scripts.verification_profiles import manifest_changes, ordinary_document

    portable_store_digest(root)
    index = current_index(root)
    if index is None or index["verification_receipt_id"] != verification["verification_receipt_id"]:
        return False
    bundle = read_bundle(root, index["bundle_id"])
    if bundle["verification"] != verification:
        return False
    gate = gate or root / ".thoth/architecture"
    import json

    path = gate / "wiki-sync" / (verification["preflight_receipt_id"] + ".json")
    if not path.is_file():
        return False
    wiki = json.loads(path.read_text(encoding="utf-8"))
    if wiki != bundle["wiki_sync"]:
        return False
    validate_archived_receipt(gate, receipt_id=wiki["wiki_sync_receipt_id"], kind="wiki-sync", payload=wiki)
    current = capture_source_manifest(root)
    changes = manifest_changes(bundle["source_manifest"], current)
    rules = tuple(map(str, load_manifest()["rule_documents"]))
    return bool(changes) and all(ordinary_document(c["path"], rules) for c in changes)
