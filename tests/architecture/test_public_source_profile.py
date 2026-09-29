"""Synthetic package fixtures prove public omissions and internal denials stay explicit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from tests.architecture import test_portable_build_paths
from tests.architecture.public_source_profile import (
    PublicPackageError,
    current_open_owner_debt,
    public_source_profile,
)
from tests.architecture.test_wiki_status_references import assert_profile_status_has_no_stale_claims
from tests.unit.test_hosted_review_deploy_scaffold import assert_hosted_review_package_contract

_PACKAGING = (
    "# Source-only package boundary\n"
    "Original internal timelines and deployment configuration are excluded.\n"
    "HOSTED_IMAGE_NOT_VERIFIED: omission is not hosted image verification.\n"
)
_NOW = (
    "# Public source preview status\n"
    "ATOMICITY DEBT: 1 OPEN\n"
    "No complete current-source FULL pass is claimed.\n"
)
_ANCHOR_TEXT = {
    "PROJECT_WIKI/NOW.md": _NOW,
    "PROJECT_WIKI/50_SEED_ROADMAP/behavioral-acceptance-contracts.md": "# Behavioral acceptance\n",
    "config/architecture-conformance.json": json.dumps(
        {"canonical_owners": [{"atomicity_debt": {"status": "OPEN"}}]}
    ),
    "docs/PACKAGING.md": _PACKAGING,
    "docs/VERIFICATION.md": "No current-source FULL pass is claimed.\n",
    "docs/architecture/backend-runtime-boundaries.md": "# Backend runtime boundaries\n",
}


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    (root / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )


def _rows(manifest: dict[str, object]) -> list[dict[str, object]]:
    value: object = manifest["files"]
    assert isinstance(value, list)
    return cast(list[dict[str, object]], value)


def _public_fixture(root: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for relative, text in sorted(_ANCHOR_TEXT.items()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        raw = path.read_bytes()
        rows.append(
            {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        )
    manifest: dict[str, object] = {
        "schema_version": "thoth-source-preview-manifest-v1",
        "snapshot_type": "MIT source-only public preview",
        "created_at": "2026-09-28T00:00:00Z",
        "license": "MIT",
        "git_history_included": False,
        "full_suite_verified": False,
        "known_selected_regression_failures": 25,
        "source_archive_sha256": "a" * 64,
        "public_repository": "https://example.invalid/thoth",
        "verification_basis": "synthetic package contract fixture, not FULL proof",
        "files": rows,
    }
    _write_manifest(root, manifest)
    return manifest


def _refresh_row(root: Path, manifest: dict[str, object], relative: str) -> None:
    raw = (root / relative).read_bytes()
    rows = _rows(manifest)
    row = next(item for item in rows if item["path"] == relative)
    row["sha256"], row["bytes"] = hashlib.sha256(raw).hexdigest(), len(raw)
    _write_manifest(root, manifest)


def test_public_fixture_has_current_anchors_and_no_hosted_image_proof(tmp_path: Path) -> None:
    _public_fixture(tmp_path)
    profile = public_source_profile(tmp_path)
    assert profile is not None
    assert current_open_owner_debt(profile) == 1
    assert_profile_status_has_no_stale_claims(tmp_path)
    assert_hosted_review_package_contract(tmp_path)


def test_missing_public_anchor_is_rejected(tmp_path: Path) -> None:
    _public_fixture(tmp_path)
    (tmp_path / "docs/VERIFICATION.md").unlink()
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


def test_listed_nonanchor_without_file_is_rejected(tmp_path: Path) -> None:
    manifest = _public_fixture(tmp_path)
    _rows(manifest).append({"path": "docs/reviewed-extra.md", "sha256": "a" * 64, "bytes": 1})
    _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError, match="missing"):
        public_source_profile(tmp_path)


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("bytes", 1)])
def test_public_anchor_digest_or_length_drift_is_rejected(
    tmp_path: Path, field: str, value: str | int
) -> None:
    manifest = _public_fixture(tmp_path)
    rows = _rows(manifest)
    row = next(item for item in rows if item["path"] == "PROJECT_WIKI/NOW.md")
    row[field] = value
    _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    ["schema", "unknown_profile", "bytes_type", "extra_field", "hash_shape", "archive_hash_shape"],
)
def test_invalid_manifest_schema_or_type_is_rejected(tmp_path: Path, mutation: str) -> None:
    manifest = _public_fixture(tmp_path)
    if mutation == "schema":
        manifest["schema_version"] = "unreviewed"
    elif mutation == "unknown_profile":
        manifest["snapshot_type"] = "unreviewed"
    elif mutation == "bytes_type":
        rows = _rows(manifest)
        rows[0]["bytes"] = "1"
    elif mutation == "hash_shape":
        _rows(manifest)[0]["sha256"] = "a" * 64 + "\n"
    elif mutation == "archive_hash_shape":
        manifest["source_archive_sha256"] = "a" * 64 + "\n"
    else:
        manifest["trust_me"] = True
    _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


def test_duplicate_public_path_is_rejected_case_insensitively(tmp_path: Path) -> None:
    manifest = _public_fixture(tmp_path)
    rows = _rows(manifest)
    original = rows[0]["path"]
    assert isinstance(original, str)
    rows.append({**rows[0], "path": original.upper()})
    _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError, match="duplicated"):
        public_source_profile(tmp_path)


@pytest.mark.parametrize("path", ["../private.md", "/absolute.md", "C:/private.md", "bad\\path"])
def test_noncanonical_or_escaping_path_is_rejected(tmp_path: Path, path: str) -> None:
    manifest = _public_fixture(tmp_path)
    rows = _rows(manifest)
    rows.append({"path": path, "sha256": "a" * 64, "bytes": 1})
    _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


@pytest.mark.parametrize("listed", [False, True])
def test_excluded_deployment_scaffold_is_rejected_as_bytes_or_listing(
    tmp_path: Path, listed: bool
) -> None:
    manifest = _public_fixture(tmp_path)
    relative = "deploy/hosted-review/.dockerignore"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(".thoth\n", encoding="utf-8")
    if listed:
        rows = _rows(manifest)
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
        _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


@pytest.mark.parametrize("listed", [False, True])
def test_excluded_internal_canonical_file_is_rejected(tmp_path: Path, listed: bool) -> None:
    manifest = _public_fixture(tmp_path)
    relative = "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("internal fixture only\n", encoding="utf-8")
    if listed:
        rows = _rows(manifest)
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
        _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


@pytest.mark.parametrize("claimed_full", [False, True])
def test_old_completion_claim_is_rejected_even_with_rehashed_manifest(
    tmp_path: Path, claimed_full: bool
) -> None:
    manifest = _public_fixture(tmp_path)
    manifest["full_suite_verified"] = claimed_full
    now = tmp_path / "PROJECT_WIKI/NOW.md"
    now.write_text(_NOW + "ratcheted exception 0\n", encoding="utf-8")
    _refresh_row(tmp_path, manifest, "PROJECT_WIKI/NOW.md")
    with pytest.raises(AssertionError):
        assert_profile_status_has_no_stale_claims(tmp_path)


@pytest.mark.parametrize("listed", [False, True])
def test_excluded_internal_wiki_page_is_rejected(tmp_path: Path, listed: bool) -> None:
    manifest = _public_fixture(tmp_path)
    relative = "PROJECT_WIKI/80_OPEN_QUESTIONS/INDEX.md"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("internal timeline\n", encoding="utf-8")
    if listed:
        _rows(manifest).append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
        _write_manifest(tmp_path, manifest)
    with pytest.raises(PublicPackageError):
        public_source_profile(tmp_path)


def test_atomicity_debt_mismatch_is_rejected_even_with_rehashed_manifest(tmp_path: Path) -> None:
    manifest = _public_fixture(tmp_path)
    now = tmp_path / "PROJECT_WIKI/NOW.md"
    now.write_text(_NOW.replace("1 OPEN", "0 OPEN"), encoding="utf-8")
    _refresh_row(tmp_path, manifest, "PROJECT_WIKI/NOW.md")
    with pytest.raises(AssertionError):
        assert_profile_status_has_no_stale_claims(tmp_path)


def test_internal_canonical_missing_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "PROJECT_WIKI/50_SEED_ROADMAP").mkdir(parents=True)
    (tmp_path / "PROJECT_WIKI/NOW.md").write_text("internal fixture\n", encoding="utf-8")
    (tmp_path / "PROJECT_WIKI/50_SEED_ROADMAP/behavioral-acceptance-contracts.md").write_text(
        "internal fixture\n", encoding="utf-8"
    )
    monkeypatch.setattr(test_portable_build_paths, "ROOT", tmp_path)
    with pytest.raises(AssertionError):
        test_portable_build_paths.test_repository_has_profile_appropriate_canonical_contract()


def test_internal_stale_wiki_still_fails(tmp_path: Path) -> None:
    pages = (
        "PROJECT_WIKI/80_OPEN_QUESTIONS/INDEX.md",
        "PROJECT_WIKI/50_SEED_ROADMAP/seed-map.md",
        "PROJECT_WIKI/50_SEED_ROADMAP/seed-1-memory-kernel.md",
        "PROJECT_WIKI/40_HACKATHON_DEMO/demo-scope.md",
    )
    for relative in pages:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("internal fixture\n", encoding="utf-8")
    (tmp_path / pages[0]).write_text(
        "A11 connector allowlist·Project Policy·sandbox policy digest fail-closed enforcement\n",
        encoding="utf-8",
    )
    (tmp_path / "PROJECT_WIKI/NOW.md").write_text("ratcheted exception 0\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_profile_status_has_no_stale_claims(tmp_path)


def test_internal_insecure_image_scaffold_still_fails(tmp_path: Path) -> None:
    path = tmp_path / "deploy/hosted-review/.dockerignore"
    path.parent.mkdir(parents=True)
    path.write_text(".thoth\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_hosted_review_package_contract(tmp_path)
