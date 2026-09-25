"""Tested files and generated evidence are separate identity domains."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

SOURCE_POLICY = "thoth-reviewable-source-v2"
GENERATED_ROOT = ".codex/verification"
_GENERATED = re.compile(
    r"^\.codex/verification/(?:current\.json|(?:bundles|indices)/[0-9a-f]{64}\.json)$"
)


def is_generated_verification_path(relative: str) -> bool:
    return bool(_GENERATED.fullmatch(relative.replace("\\", "/")))


def git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise ValueError("cannot establish verification Git identity")
    return result.stdout


def reviewed_paths(root: Path) -> tuple[str, ...]:
    values = git_bytes(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return tuple(
        raw.decode("utf-8")
        for raw in sorted(set(values.split(b"\0")) - {b""})
        if not is_generated_verification_path(raw.decode("utf-8"))
    )


def repository_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in reviewed_paths(root):
        path = root / relative
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"MISSING")
    return digest.hexdigest()


def index_digest(root: Path) -> str:
    return hashlib.sha256(git_bytes(root, "ls-files", "--stage", "-z")).hexdigest()


def assert_unchanged(root: Path, source: str, index: str) -> None:
    if repository_digest(root) != source or index_digest(root) != index:
        raise ValueError("source/index changed during verification; run a fresh gate")


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def capture_source_manifest(root: Path) -> dict[str, object]:
    files = []
    digest = hashlib.sha256()
    for relative in reviewed_paths(root):
        path = root / relative
        data = path.read_bytes() if path.is_file() else None
        sha = None if data is None else hashlib.sha256(data).hexdigest()
        digest.update(relative.encode() + b"\0")
        digest.update(b"MISSING" if sha is None else bytes.fromhex(sha))
        files.append({"path": relative, "sha256": sha, "size": None if data is None else len(data)})
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "policy": SOURCE_POLICY,
        "head": git_bytes(root, "rev-parse", "HEAD").decode().strip(),
        "repository_digest": digest.hexdigest(),
        "index_digest": index_digest(root),
        "files": files,
    }
    return {**draft, "manifest_digest": _hash_json(draft)}


def validate_source_manifest(value: dict[str, object]) -> None:
    expected = {
        "schema_version",
        "policy",
        "head",
        "repository_digest",
        "index_digest",
        "files",
        "manifest_digest",
    }
    if (
        set(value) != expected
        or value["schema_version"] != "1.0.0"
        or value["policy"] != SOURCE_POLICY
    ):
        raise ValueError("unsupported tested-source manifest")
    if not isinstance(value["head"], str) or not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value["head"]
    ):
        raise ValueError("manifest Git head is invalid")
    if value["manifest_digest"] != _hash_json(
        {key: item for key, item in value.items() if key != "manifest_digest"}
    ):
        raise ValueError("tested-source manifest identity differs")
    entries = value["files"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("tested-source manifest has no files")
    names = []
    digest = hashlib.sha256()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise ValueError("invalid tested-source file entry")
        relative, sha, size = item["path"], item["sha256"], item["size"]
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or "\x00" in relative
            or ":" in relative
        ):
            raise ValueError("invalid tested-source file path")
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative
            or is_generated_verification_path(relative)
        ):
            raise ValueError("manifest includes escaped or generated verification data")
        if sha is None:
            if size is not None:
                raise ValueError("missing file has a size")
        elif (
            not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or type(size) is not int
            or size < 0
        ):
            raise ValueError("invalid tested-source file digest or size")
        names.append(relative)
        digest.update(relative.encode() + b"\0")
        digest.update(b"MISSING" if sha is None else bytes.fromhex(sha))
    if names != sorted(set(names)):
        raise ValueError("tested-source paths are duplicated or unsorted")
    if digest.hexdigest() != value["repository_digest"]:
        raise ValueError("tested-source aggregate differs")
    if not isinstance(value["index_digest"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["index_digest"]
    ):
        raise ValueError("tested-source index identity is invalid")


def source_matches(root: Path, manifest: dict[str, object]) -> bool:
    validate_source_manifest(manifest)
    current = capture_source_manifest(root)
    # Staging after verification is allowed only through the independent exact-tree checkpoint.
    return current["repository_digest"] == manifest["repository_digest"]
