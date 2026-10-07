"""The public file list is rebuilt by one script and a stale list says how to refresh it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import update_source_manifest as tool
from tests.architecture.public_source_profile import PublicPackageError, public_source_profile

HEADER = {
    "schema_version": "thoth-source-preview-manifest-v1",
    "created_at": "2026-01-01T00:00:00+00:00",
    "license": "MIT",
    "git_history_included": False,
    "full_suite_verified": False,
    "known_selected_regression_failures": 3,
    "files": [],
    "source_archive_sha256": "a" * 64,
    "public_repository": "https://example.invalid/repo",
    "snapshot_type": "MIT source-only public preview",
    "verification_basis": "Kept as written.",
}
PACKAGING = (
    "# Source-only package boundary\n"
    "deployment configuration is excluded.\n"
    "HOSTED_IMAGE_NOT_VERIFIED\n"
)
ANCHORS = {
    "docs/architecture/rpc-method-catalog.md": "# rpc\n",
    "config/architecture-conformance.json": json.dumps(
        {"canonical_owners": [{"atomicity_debt": {"status": "OPEN"}}]}
    ),
    "docs/PACKAGING.md": PACKAGING,
    "docs/VERIFICATION.md": "# verification\n",
    "docs/architecture/backend-runtime-boundaries.md": "# boundaries\n",
}


def _tree(root: Path, extra: dict[str, str] | None = None) -> list[str]:
    files = {**ANCHORS, "b.txt": "second\n", "a/z.txt": "first\n", **(extra or {})}
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (root / "SOURCE_MANIFEST.json").write_text("{}", encoding="utf-8")
    return [*files, "SOURCE_MANIFEST.json"]


def test_build_skips_the_list_itself_sorts_paths_keeps_the_header_and_renews_created_at(
    tmp_path: Path,
) -> None:
    paths = _tree(tmp_path)
    built = tool.build_manifest(
        tmp_path, paths, dict(HEADER), created_at="2026-10-04T00:00:00+00:00"
    )
    listed = [row["path"] for row in built["files"]]
    assert listed == sorted(listed) and "SOURCE_MANIFEST.json" not in listed
    assert set(listed) == set(ANCHORS) | {"b.txt", "a/z.txt"}
    assert built["created_at"] == "2026-10-04T00:00:00+00:00"
    assert list(built) == list(HEADER)
    for key, expected in HEADER.items():
        if key not in {"files", "created_at"}:
            assert built[key] == expected
    row = next(item for item in built["files"] if item["path"] == "b.txt")
    assert row == {
        "path": "b.txt",
        "sha256": hashlib.sha256(b"second\n").hexdigest(),
        "bytes": 7,
    }


def test_build_refuses_a_header_with_other_fields(tmp_path: Path) -> None:
    paths = _tree(tmp_path)
    with pytest.raises(ValueError, match="header"):
        tool.build_manifest(tmp_path, paths, {**HEADER, "extra": 1}, created_at="x")
    with pytest.raises(ValueError, match="header"):
        tool.build_manifest(
            tmp_path,
            paths,
            {key: value for key, value in HEADER.items() if key != "license"},
            created_at="x",
        )


def test_compare_counts_changed_unlisted_and_missing_files(tmp_path: Path) -> None:
    paths = _tree(tmp_path)
    manifest = tool.build_manifest(tmp_path, paths, dict(HEADER), created_at="t")
    assert tool.compare(tmp_path, manifest, paths).clean
    (tmp_path / "b.txt").write_bytes(b"changed bytes\n")
    (tmp_path / "new.txt").write_bytes(b"new\n")
    (tmp_path / "a/z.txt").unlink()
    current = [path for path in [*paths, "new.txt"] if path != "a/z.txt"]
    difference = tool.compare(tmp_path, manifest, current)
    assert difference.changed == ("b.txt",)
    assert difference.unlisted == ("new.txt",)
    assert difference.missing == ("a/z.txt",)
    assert not difference.clean
    report = tool.format_report(difference)
    assert "1" in report and "b.txt" in report and "new.txt" in report and "a/z.txt" in report


def test_the_report_lists_at_most_twenty_names_and_counts_the_rest(tmp_path: Path) -> None:
    names = tuple(f"f{index:03}.txt" for index in range(25))
    report = tool.format_report(tool.Difference(changed=names, unlisted=(), missing=()))
    assert "f019.txt" in report and "f020.txt" not in report and "5" in report


def test_a_rebuilt_list_passes_the_public_source_profile_check(tmp_path: Path) -> None:
    paths = _tree(tmp_path)
    manifest = tool.build_manifest(
        tmp_path, paths, dict(HEADER), created_at="2026-10-04T00:00:00+00:00"
    )
    (tmp_path / "SOURCE_MANIFEST.json").write_bytes(tool.render(manifest))
    profile = public_source_profile(tmp_path)
    assert profile is not None and set(profile.files) == {row["path"] for row in manifest["files"]}
    (tmp_path / "b.txt").write_bytes(b"edited after the list was made\n")
    with pytest.raises(PublicPackageError, match="update_source_manifest"):
        public_source_profile(tmp_path)


def test_render_uses_lf_utf8_without_bom_and_a_final_newline(tmp_path: Path) -> None:
    paths = _tree(tmp_path)
    manifest = tool.build_manifest(tmp_path, paths, dict(HEADER), created_at="t")
    raw = tool.render(manifest)
    assert raw.endswith(b"}\n") and b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw) == manifest
