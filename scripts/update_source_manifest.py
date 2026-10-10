"""Rebuild or check SOURCE_MANIFEST.json, the byte inventory of the public source files.

Run `python scripts/update_source_manifest.py` after the last file change and before opening a
public pull request, then commit the result. `--check` leaves the repository unchanged: it lists
what differs and exits 1 when the list is stale. Git clean conversion runs in disposable storage.

The list names every file that is tracked (or untracked but not ignored) except the list itself,
whose hash would depend on its own bytes. This is the same rule the current list follows. The
header fields are kept as written; only `created_at` is renewed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, TypedDict, cast

MANIFEST_NAME = "SOURCE_MANIFEST.json"
EXCLUDED_PATHS = frozenset({MANIFEST_NAME})
HEADER_FIELDS = frozenset(
    {
        "schema_version",
        "snapshot_type",
        "created_at",
        "license",
        "git_history_included",
        "full_suite_verified",
        "known_selected_regression_failures",
        "source_archive_sha256",
        "public_repository",
        "verification_basis",
        "files",
    }
)
MAX_LISTED = 20
REFRESH_HINT = (
    "Run `python scripts/update_source_manifest.py` to rebuild the list, then commit it. "
    "(python scripts/update_source_manifest.py 를 실행해 다시 만든 뒤 커밋하세요.)"
)

Manifest = dict[str, object]


class FileRow(TypedDict):
    path: str
    sha256: str
    bytes: int


class _Reconfigurable(Protocol):
    def reconfigure(self, *, encoding: str) -> None: ...


@dataclass(frozen=True)
class Difference:
    changed: tuple[str, ...]
    unlisted: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.changed or self.unlisted or self.missing)


def list_public_paths(root: Path) -> list[str]:
    """Files that belong to the public tree: tracked or not ignored, present as regular files."""
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    names = {name for name in output.split("\0") if name}
    return sorted(
        name for name in names if (root / name).is_file() and not (root / name).is_symlink()
    )


def read_source_bytes(root: Path, paths: list[str]) -> dict[str, bytes]:
    """Read working files as Git add would store them, or raw bytes in a source export.

    hash-object does not load the index and differs from add for historical text=auto CRLF
    blobs. Use add with a disposable index and object directory instead. The real index,
    objects, refs and working files are never written. Configured clean filters still run.
    """
    if not paths:
        return {}
    root = root.resolve()
    if not (root / ".git").exists():
        return {relative: (root / relative).read_bytes() for relative in paths}

    def git(*args: str, data: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=data,
            capture_output=True,
            check=True,
            env=env,
        ).stdout

    # Preserve index contents (including the CRLF exception), but not stat caches or flags:
    # every requested path must be read again, including unstaged and untracked content.
    entries = git("ls-files", "--stage", "-z")
    objects = git("rev-parse", "--path-format=absolute", "--git-path", "objects")
    with TemporaryDirectory(prefix="thoth-manifest-") as temporary:
        scratch = Path(temporary)
        (scratch / "objects").mkdir()
        (scratch / "hooks").mkdir()
        alternates = json.dumps(objects.decode("utf-8").strip(), ensure_ascii=False)
        if inherited := os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            alternates += os.pathsep + inherited
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(scratch / "index"),
            "GIT_OBJECT_DIRECTORY": str(scratch / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": alternates,
            "GIT_LITERAL_PATHSPECS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        options = (
            "-c",
            "core.splitIndex=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={scratch / 'hooks'}",
        )
        git(*options, "update-index", "-z", "--index-info", data=entries, env=env)
        git(
            *options,
            "add",
            "--sparse",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            data=b"\0".join(path.encode("utf-8") for path in paths) + b"\0",
            env=env,
        )
        staged: dict[str, bytes] = {}
        for entry in git(*options, "ls-files", "--stage", "-z", env=env).split(b"\0"):
            if entry:
                metadata, name = entry.split(b"\t", 1)
                _, oid, stage = metadata.split()
                if stage == b"0":
                    staged[name.decode("utf-8")] = oid
        oids = [staged[path] for path in paths]
        output = io.BytesIO(git("cat-file", "--batch", data=b"\n".join(oids) + b"\n", env=env))
        result: dict[str, bytes] = {}
        for path, oid in zip(paths, oids, strict=True):
            actual, kind, size = output.readline().split()
            if actual != oid or kind != b"blob":
                raise ValueError(f"Git did not return a source blob: {path}")
            data = output.read(int(size))
            if len(data) != int(size) or output.read(1) != b"\n":
                raise ValueError(f"Git returned an incomplete source blob: {path}")
            result[path] = data
        return result


def _row(relative: str, data: bytes) -> FileRow:
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _listed(paths: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({path for path in paths if path not in EXCLUDED_PATHS})


def build_manifest(
    root: Path, paths: list[str], previous: Manifest, *, created_at: str
) -> Manifest:
    """A new list over `paths`; every header field of `previous` stays as it was."""
    if frozenset(previous) != HEADER_FIELDS:
        raise ValueError("the previous list has a different header than the checker accepts")
    built: Manifest = {}
    for key, value in previous.items():
        if key == "created_at":
            built[key] = created_at
        elif key == "files":
            built[key] = [
                _row(relative, data)
                for relative, data in read_source_bytes(root, _listed(paths)).items()
            ]
        else:
            built[key] = value
    return built


def compare(root: Path, manifest: Manifest, paths: list[str]) -> Difference:
    files = manifest["files"]
    assert isinstance(files, list)
    rows = {item["path"]: item for item in cast(list[FileRow], files)}
    current = set(_listed(paths))
    contents = read_source_bytes(root, sorted(rows.keys() & current))
    changed = sorted(
        relative
        for relative in rows.keys() & current
        if {k: v for k, v in _row(relative, contents[relative]).items() if k != "path"}
        != {k: v for k, v in rows[relative].items() if k != "path"}
    )
    return Difference(
        changed=tuple(changed),
        unlisted=tuple(sorted(current - rows.keys())),
        missing=tuple(sorted(rows.keys() - current)),
    )


def render(manifest: Manifest) -> bytes:
    return (json.dumps(manifest, indent=2) + "\n").encode("utf-8")


def format_report(difference: Difference) -> str:
    lines: list[str] = []
    for label, names in (
        ("changed", difference.changed),
        ("not in the list", difference.unlisted),
        ("listed but missing", difference.missing),
    ):
        lines.append(f"{label}: {len(names)}")
        lines.extend(f"  {name}" for name in names[:MAX_LISTED])
        if len(names) > MAX_LISTED:
            lines.append(f"  ... and {len(names) - MAX_LISTED} more")
    return "\n".join(lines)


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "Source manifest").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare only; exit 1 if stale")
    options = parser.parse_args(argv)
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    path = root / MANIFEST_NAME
    previous: Manifest = json.loads(path.read_bytes())
    paths = list_public_paths(root)
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):
        cast(_Reconfigurable, stdout).reconfigure(encoding="utf-8")
    if options.check:
        difference = compare(root, previous, paths)
        if difference.clean:
            print(f"{MANIFEST_NAME} is current.")
            return 0
        print(format_report(difference))
        print(REFRESH_HINT)
        return 1
    built = build_manifest(root, paths, previous, created_at=datetime.now(UTC).isoformat())
    path.write_bytes(render(built))
    files = built["files"]
    assert isinstance(files, list)
    print(f"{MANIFEST_NAME} rebuilt: {len(cast(list[object], files))} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
