"""Rebuild or check SOURCE_MANIFEST.json, the byte inventory of the public source files.

Run `python scripts/update_source_manifest.py` after the last file change and before opening a
public pull request, then commit the result. `--check` writes nothing: it lists what differs and
exits 1 when the list is stale.

The list names every file that is tracked (or untracked but not ignored) except the list itself,
whose hash would depend on its own bytes. This is the same rule the current list follows. The
header fields are kept as written; only `created_at` is renewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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


def _row(root: Path, relative: str) -> dict[str, object]:
    data = (root / relative).read_bytes()
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _listed(paths: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({path for path in paths if path not in EXCLUDED_PATHS})


def build_manifest(
    root: Path, paths: list[str], previous: Manifest, *, created_at: str
) -> Manifest:
    """A new list over `paths`; every header field of `previous` stays as it was."""
    if set(previous) != HEADER_FIELDS:
        raise ValueError("the previous list has a different header than the checker accepts")
    built: Manifest = {}
    for key, value in previous.items():
        if key == "created_at":
            built[key] = created_at
        elif key == "files":
            built[key] = [_row(root, relative) for relative in _listed(paths)]
        else:
            built[key] = value
    return built


def compare(root: Path, manifest: Manifest, paths: list[str]) -> Difference:
    files = manifest["files"]
    assert isinstance(files, list)
    rows: dict[str, dict[str, object]] = {item["path"]: item for item in files}
    current = set(_listed(paths))
    changed = sorted(
        relative
        for relative in rows.keys() & current
        if {k: v for k, v in _row(root, relative).items() if k != "path"}
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare only; exit 1 if stale")
    options = parser.parse_args(argv)
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    path = root / MANIFEST_NAME
    previous: Manifest = json.loads(path.read_bytes())
    paths = list_public_paths(root)
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):
        stdout.reconfigure(encoding="utf-8")
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
    print(f"{MANIFEST_NAME} rebuilt: {len(files)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
