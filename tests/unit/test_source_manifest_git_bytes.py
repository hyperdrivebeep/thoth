"""Manifest bytes follow real Git add, including unstaged files and clean attributes."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from scripts import update_source_manifest as tool
from tests.architecture.public_source_profile import PublicPackageError, public_source_profile
from tests.unit.test_update_source_manifest import ANCHORS

HEADER: tool.Manifest = {
    "schema_version": "thoth-source-preview-manifest-v1",
    "created_at": "t",
    "license": "MIT",
    "git_history_included": False,
    "full_suite_verified": False,
    "known_selected_regression_failures": 0,
    "files": [],
    "source_archive_sha256": "a" * 64,
    "public_repository": "https://example.invalid/repo",
    "snapshot_type": "MIT source-only public preview",
    "verification_basis": "Synthetic Git conversion fixture only.",
}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


def _init(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.safecrlf", "false")
    _git(root, "config", "core.attributesFile", str(root / "no-global-attributes"))


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _build(root: Path) -> tool.Manifest:
    return tool.build_manifest(root, tool.list_public_paths(root), dict(HEADER), created_at="t")


def _rows(manifest: tool.Manifest) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], manifest["files"])


def _assert_staged_bytes(root: Path, manifest: tool.Manifest) -> None:
    _git(root, "add", "--all")
    for row in _rows(manifest):
        raw = _git(root, "show", ":" + str(row["path"]))
        assert row["bytes"] == len(raw)
        assert row["sha256"] == hashlib.sha256(raw).hexdigest()


def test_lf_and_crlf_worktrees_match_and_leave_real_git_storage_unchanged(tmp_path: Path) -> None:
    manifests: list[tool.Manifest] = []
    for name, eol, autocrlf in (("lf", b"\n", "false"), ("crlf", b"\r\n", "true")):
        root = tmp_path / name
        _init(root)
        (root / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
        (root / "tracked.txt").write_bytes(b"before\n")
        _git(root, "add", ".")
        _git(root, "config", "core.autocrlf", autocrlf)
        (root / "tracked.txt").write_bytes(b"unstaged" + eol)
        (root / "new space 한글.txt").write_bytes(b"untracked" + eol)
        (root / ".gitignore").write_bytes(b"ignored.txt\n")
        (root / "ignored.txt").write_bytes(b"not public")
        before = _snapshot(root)
        manifest = _build(root)
        assert tool.compare(root, manifest, tool.list_public_paths(root)).clean
        assert _snapshot(root) == before
        rows = {str(row["path"]): row for row in _rows(manifest)}
        assert "ignored.txt" not in rows
        assert rows["tracked.txt"]["sha256"] == hashlib.sha256(b"unstaged\n").hexdigest()
        assert rows["new space 한글.txt"]["bytes"] == len(b"untracked\n")
        _assert_staged_bytes(root, manifest)
        manifests.append(manifest)
    assert manifests[0] == manifests[1]


def test_existing_crlf_index_is_preserved_exactly_as_git_add(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "old.txt").write_bytes(b"old\r\n")
    _git(tmp_path, "add", "old.txt")
    (tmp_path / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    (tmp_path / "old.txt").write_bytes(b"edited\r\n")
    manifest = _build(tmp_path)
    row = next(row for row in _rows(manifest) if row["path"] == "old.txt")
    assert row["sha256"] == hashlib.sha256(b"edited\r\n").hexdigest()
    _assert_staged_bytes(tmp_path, manifest)


def test_git_attributes_cover_binary_exact_bytes_ident_encoding_and_clean_filter(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    (tmp_path / ".gitattributes").write_bytes(
        b"* text=auto eol=lf\n*.bin binary\n*.exact -text\n*.cmd text eol=crlf\n"
        b"*.id ident\n*.wide text working-tree-encoding=UTF-16\n*.upper filter=upper\n"
    )
    command = (
        f'"{Path(sys.executable).as_posix()}" -c '
        '"import sys;sys.stdout.buffer.write(sys.stdin.buffer.read().upper())"'
    )
    _git(tmp_path, "config", "filter.upper.clean", command)
    _git(tmp_path, "config", "filter.upper.required", "true")
    payloads = {
        "binary.bin": b"\x00\r\n\xff",
        "preserve.exact": b"exact\r\n",
        "run.cmd": b"echo yes\r\n",
        "expanded.id": b"$Id: deadbeef $\r\n",
        "utf16.wide": "wide\r\n".encode("utf-16"),
        "clean.upper": b"lower\r\n",
    }
    for name, raw in payloads.items():
        (tmp_path / name).write_bytes(raw)
    before = _snapshot(tmp_path)
    clean = tool.read_source_bytes(tmp_path, list(payloads))
    assert clean == {
        "binary.bin": payloads["binary.bin"],
        "preserve.exact": payloads["preserve.exact"],
        "run.cmd": b"echo yes\n",
        "expanded.id": b"$Id$\n",
        "utf16.wide": b"wide\n",
        "clean.upper": b"LOWER\n",
    }
    assert _snapshot(tmp_path) == before
    _assert_staged_bytes(tmp_path, _build(tmp_path))


def test_required_clean_failure_does_not_fall_back_or_modify_repository(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / ".gitattributes").write_bytes(b"*.txt filter=broken\n")
    (tmp_path / "file.txt").write_bytes(b"not silently accepted\r\n")
    _git(tmp_path, "config", "filter.broken.clean", "exit 1")
    _git(tmp_path, "config", "filter.broken.required", "true")
    before = _snapshot(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        _build(tmp_path)
    assert _snapshot(tmp_path) == before


def test_check_mode_reports_real_content_drift_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init(tmp_path)
    (tmp_path / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    (tmp_path / "text.txt").write_bytes(b"first\r\n")
    (tmp_path / tool.MANIFEST_NAME).write_bytes(tool.render(_build(tmp_path)))
    before = _snapshot(tmp_path)
    assert tool.main(["--check"], root=tmp_path) == 0
    assert _snapshot(tmp_path) == before
    (tmp_path / "text.txt").write_bytes(b"changed\r\n")
    before = _snapshot(tmp_path)
    assert tool.main(["--check"], root=tmp_path) == 1
    assert _snapshot(tmp_path) == before
    output = capsys.readouterr().out
    assert "changed: 1" in output and tool.REFRESH_HINT in output


def test_public_checker_uses_clean_bytes_and_rejects_content_drift(tmp_path: Path) -> None:
    _init(tmp_path)
    for relative, text in ANCHORS.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (tmp_path / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    (tmp_path / "b.txt").write_bytes(b"second\r\n")
    (tmp_path / tool.MANIFEST_NAME).write_bytes(tool.render(_build(tmp_path)))
    before = _snapshot(tmp_path)
    profile = public_source_profile(tmp_path)
    assert profile is not None and profile.read_bytes("b.txt") == b"second\n"
    assert _snapshot(tmp_path) == before
    (tmp_path / "b.txt").write_bytes(b"edited\r\n")
    with pytest.raises(PublicPackageError, match="bytes differ"):
        profile.read_bytes("b.txt")


def test_export_without_git_keeps_raw_bytes_even_inside_a_repository(tmp_path: Path) -> None:
    _init(tmp_path)
    root = tmp_path / "export"
    root.mkdir()
    (root / ".gitattributes").write_bytes(b"* text eol=lf\n")
    (root / "file.txt").write_bytes(b"raw\r\n")
    assert tool.read_source_bytes(root, ["file.txt"]) == {"file.txt": b"raw\r\n"}


def test_git_directory_pointer_and_split_index_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    _init(root)
    (root / ".gitattributes").write_bytes(b"* text eol=lf\n")
    (root / "file.txt").write_bytes(b"before\n")
    _git(root, "add", ".")
    _git(root, "update-index", "--split-index")
    metadata = tmp_path / "metadata"
    (root / ".git").rename(metadata)
    (root / ".git").write_bytes(f"gitdir: {metadata.as_posix()}\n".encode())
    (root / "file.txt").write_bytes(b"after\r\n")
    before = _snapshot(tmp_path)
    assert tool.read_source_bytes(root, ["file.txt"]) == {"file.txt": b"after\n"}
    assert _snapshot(tmp_path) == before
