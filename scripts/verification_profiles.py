"""Conservative two-profile policy. Selection uses source manifests, never a declared scope."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from scripts.required_architecture_checks import CHECKS

CONTROL_PATHS = frozenset({
    "conftest.py", "scripts/pytest_environment.py", "config/inactive-development-hook-tests.json",
    ".codex/hooks.json",
    "AGENTS.md", "Makefile.ps1", "scripts/verification_profiles.py",
    "scripts/verification_profile_contract.py", "scripts/complete_architecture_gate.py",
    "scripts/architecture_gate_contract.py", "scripts/verification_bundle_contract.py",
    "scripts/verification_identity.py", "scripts/verification_progress.py",
    "scripts/required_architecture_checks.py", "scripts/architecture_contract.py",
    "scripts/checkpoint_commit_contract.py", "config/architecture-conformance.json",
    "docs/architecture/verification-profiles.md", "docs/architecture/agent-execution-rules.md",
    "PROJECT_WIKI/50_SEED_ROADMAP/behavioral-acceptance-contracts.md",
    "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md",
    "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md",
})
TOOLING_PATHS = frozenset({
    ".codex/hooks/pre_tool_policy.py", ".codex/hooks/stop_acceptance_gate.py",
    ".codex/hooks/session_start.py", "scripts/hook_owner_contract.py",
    "scripts/read_only_command_contract.py", "scripts/python_read_contract.py",
    "scripts/parse_shell_units.ps1", "scripts/verification_status.py",
    "scripts/pytest_progress.py", "scripts/stop_progress_contract.py",
})
FULL_STAGES = (
    "ARCHITECTURE", "LINT", "TYPE", "BACKEND", "WEB_LINT", "WEB_TYPE",
    "WEB_TEST", "WEB_BUILD", "DOCTOR",
)
TOOLING_STAGES = ("ARCHITECTURE", "LINT", "TYPE", "TOOLING")
DOCUMENT_ROOTS = ("docs/verification/", "docs/plans/", "PROJECT_WIKI/", "research-briefs/")


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def policy_definition() -> dict[str, Any]:
    return {
        "version": "thoth-verification-profiles-v2",
        "inactive_development_hook_manifest": "config/inactive-development-hook-tests.json",
        "test_scope": "ALL_ACTIVE_ENVIRONMENT_TESTS",
        "control_paths": sorted(CONTROL_PATHS), "tooling_paths": sorted(TOOLING_PATHS),
        "test_pattern": "tests/architecture/test_*.py",
        "companion_document_roots": list(DOCUMENT_ROOTS),
        "tooling_stages": list(TOOLING_STAGES), "full_stages": list(FULL_STAGES),
        "tooling_pytest_targets": ["tests/architecture"], "full_pytest_targets": ["tests"],
        "architecture_checks": list(CHECKS),
        "deletion_requires_full": True, "checkpoint_requires_full": True,
        "tooling_requires_passed_cases": True,
    }


def manifest_changes(
    baseline: dict[str, Any] | None, current: dict[str, Any]
) -> list[dict[str, Any]]:
    before = {} if baseline is None else {f["path"]: f["sha256"] for f in baseline["files"]}
    after = {f["path"]: f["sha256"] for f in current["files"]}
    changed = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if path in before and path in after and old == new:
            continue
        changed.append({
            "path": path, "before_sha256": old, "after_sha256": new,
            "kind": "DELETED" if new is None else "ADDED" if old is None else "MODIFIED",
        })
    return changed


def ordinary_document(path: str, rule_documents: tuple[str, ...]) -> bool:
    return path not in CONTROL_PATHS and path not in rule_documents and (
        path.endswith(".md") and path.startswith(DOCUMENT_ROOTS)
    )


def _tool_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return path in TOOLING_PATHS or (
        candidate.parent.as_posix() == "tests/architecture"
        and candidate.name.startswith("test_") and candidate.suffix == ".py"
    )


def select_manifest_profile(
    current: dict[str, Any], *, baseline: dict[str, Any] | None = None,
    baseline_id: str | None = None, baseline_policy: dict[str, Any] | None = None,
    rule_documents: tuple[str, ...] = (), force_full: bool = False,
    runtime: dict[str, str] | None = None, baseline_runtime: dict[str, str] | None = None,
) -> dict[str, Any]:
    changes = manifest_changes(baseline, current)
    reasons: set[str] = set()
    policy = policy_definition()
    if force_full:
        reasons.add("EXPLICIT_FULL")
    if baseline is None or baseline_id is None:
        reasons.add("NO_VALID_FULL_BASELINE")
    elif baseline_policy != policy:
        reasons.add("BASELINE_POLICY_UNAVAILABLE_OR_CHANGED")
    else:
        before = {f["path"]: f["sha256"] for f in baseline["files"]}
        after = {f["path"]: f["sha256"] for f in current["files"]}
        if any(not before.get(p) or before.get(p) != after.get(p) for p in CONTROL_PATHS):
            reasons.add("VERIFICATION_POLICY_SOURCE_CHANGED")
        if baseline["head"] != current["head"] or baseline["index_digest"] != current["index_digest"]:
            reasons.add("BASELINE_HEAD_OR_INDEX_CHANGED")
        if runtime is not None and runtime != baseline_runtime:
            reasons.add("BASELINE_RUNTIME_CHANGED")
    for change in changes:
        path = change["path"]
        if change["kind"] == "DELETED":
            reasons.add("DELETION_OR_RENAME")
        if path in CONTROL_PATHS or path in rule_documents:
            reasons.add("RULE_OR_POLICY_CHANGED")
        elif not _tool_path(path) and not ordinary_document(path, rule_documents):
            reasons.add("PRODUCT_SHARED_OR_UNCLASSIFIED_CHANGE")
    if not any(_tool_path(c["path"]) for c in changes):
        reasons.add("NO_TOOLING_CODE_CHANGE")
    profile = "FULL" if reasons else "TOOLING"
    return {
        "schema_version": "1.0.0", "profile": profile, "requires_full_suite": profile == "FULL",
        "policy": copy.deepcopy(policy), "policy_digest": canonical_digest(policy),
        "baseline_bundle_id": baseline_id,
        "baseline_source_digest": None if baseline is None else baseline["repository_digest"],
        "current_source_digest": current["repository_digest"], "changed_files": changes,
        "rule_documents": sorted(set(rule_documents)), "force_full": force_full,
        "runtime": copy.deepcopy(runtime),
        "reason_codes": sorted(reasons) if reasons else ["ONLY_APPROVED_TOOLING_CHANGED"],
        "expected_stages": list(FULL_STAGES if profile == "FULL" else TOOLING_STAGES),
        "pytest_targets": ["tests"] if profile == "FULL" else ["tests/architecture"],
    }
