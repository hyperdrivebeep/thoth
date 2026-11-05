"""Preview and delete stored objects that nothing in a workspace refers to.

The object store names a file after its SHA-256 and has no delete. Several writers share it, and
some of them (evaluation outputs) record the digest only inside a JSON field, so "unused" is decided
by what is left after collecting every 64-character hex string the workspace holds: every value of
every table in the database and every file outside the object store itself. An object whose name is
not in that set is a candidate. Reading too much only keeps files, so the rule errs towards keeping.

The workspace lock is taken before anything is read and held until the last file is handled, so no
server can write an object between the scan and the delete. Any read that fails stops the run with
nothing deleted; a preview that could not read everything is reported as incomplete and gets no list
hash. Objects younger than 24 hours are never candidates, which covers the moment between a writer
putting a file and recording its digest after a crash.

Left out of the file scan on purpose:
- objects/ and tmp/ (the store and its staging area);
- the database file and its sidecars, which are read through SQL (raw pages would still hold the
  digests of rows that were deleted);
- diagnostics/object-prune/ (this tool's own lists and log name candidates and would protect them);
- runtime.lock (the operating system forbids reading its locked byte, and it holds no digests).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.storage.workspace_owner import acquire_workspace_owner

MIN_AGE_SECONDS = 24 * 60 * 60
PRUNE_DIRECTORY = Path("diagnostics") / "object-prune"
PRUNE_LOG = "prune-log.jsonl"
DATABASE_RELATIVE = Path("db") / "thoth.sqlite3"

_DIGEST_AT_ANY_OFFSET = re.compile(rb"(?=([0-9a-fA-F]{64}))")
_OBJECT_NAME = re.compile(r"[0-9a-f]{64}")
_FAN_NAME = re.compile(r"[0-9a-f]{2}")
_CHUNK = 4 * 1024 * 1024
_OVERLAP = 63
_SKIPPED_DIRECTORIES = frozenset({"objects", "tmp", PRUNE_DIRECTORY.as_posix()})
_SKIPPED_FILES = frozenset(
    {
        "runtime.lock",
        *(f"{DATABASE_RELATIVE.as_posix()}{suffix}" for suffix in ("", "-wal", "-shm", "-journal")),
    }
)


class ObjectPruneRefused(RuntimeError):
    """The request cannot be carried out; nothing was changed."""


@dataclass(frozen=True)
class ObjectEntry:
    digest: str
    size: int
    modified: int


@dataclass(frozen=True)
class PreviewReport:
    problems: tuple[str, ...]
    candidates: tuple[ObjectEntry, ...]
    total_bytes: int
    list_sha256: str | None
    list_path: Path | None

    @property
    def complete(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class DeleteReport:
    problems: tuple[str, ...]
    deleted_count: int
    deleted_bytes: int
    skipped_count: int
    list_sha256: str


def digests_in(data: bytes) -> set[str]:
    """Every 64-hex window in the bytes, lower-cased; longer hex runs count at each offset."""
    return {
        match.group(1).decode("ascii").lower() for match in _DIGEST_AT_ANY_OFFSET.finditer(data)
    }


def collect_references(workspace: Path) -> tuple[frozenset[str], tuple[str, ...]]:
    """Digests the workspace mentions, plus a reason for everything that could not be read."""
    found: set[str] = set()
    problems: list[str] = []
    _scan_database(workspace / DATABASE_RELATIVE, found, problems)
    for path in _workspace_files(workspace, problems):
        _scan_file(path, workspace, found, problems)
    return frozenset(found), tuple(problems)


def _scan_database(database: Path, found: set[str], problems: list[str]) -> None:
    if not database.is_file():
        problems.append(f"database not found: {DATABASE_RELATIVE.as_posix()}")
        return
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN")
            tables = [
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            ]
            connection.text_factory = bytes
            for table in tables:
                _scan_table(connection, table, found, problems)
    except sqlite3.Error as error:
        problems.append(f"database could not be read: {error}")


def _scan_table(
    connection: sqlite3.Connection, table: str, found: set[str], problems: list[str]
) -> None:
    quoted = '"' + table.replace('"', '""') + '"'
    try:
        cursor = connection.execute(f"SELECT * FROM {quoted}")
        while rows := cursor.fetchmany(500):
            for row in rows:
                for value in row:
                    if isinstance(value, bytes):
                        found |= digests_in(value)
    except sqlite3.Error as error:
        problems.append(f"table {table} could not be read: {error}")


def _workspace_files(workspace: Path, problems: list[str]) -> Iterator[Path]:
    def listing_failed(error: OSError) -> None:
        problems.append(f"folder could not be listed: {error.filename}: {error.strerror}")

    for directory, names, files in os.walk(workspace, onerror=listing_failed):
        base = Path(directory)
        relative = base.relative_to(workspace)
        names[:] = [
            name
            for name in names
            if (relative / name).as_posix().lower() not in _SKIPPED_DIRECTORIES
        ]
        for name in files:
            if (relative / name).as_posix().lower() not in _SKIPPED_FILES:
                yield base / name


def _scan_file(path: Path, workspace: Path, found: set[str], problems: list[str]) -> None:
    try:
        with path.open("rb") as stream:
            carry = b""
            while chunk := stream.read(_CHUNK):
                window = carry + chunk
                found |= digests_in(window)
                carry = window[-_OVERLAP:]
    except OSError as error:
        label = path.relative_to(workspace).as_posix()
        problems.append(f"file could not be read: {label}: {error.strerror or error}")


def _object_root(workspace: Path) -> Path:
    return workspace / "objects" / "sha256"


def inspect_object(
    path: Path, workspace: Path, references: frozenset[str], now: float
) -> ObjectEntry | None:
    """The entry if this exact path is an old, unreferenced object file; otherwise None."""
    digest = path.name
    if _OBJECT_NAME.fullmatch(digest) is None or path.parent.name != digest[:2]:
        return None
    if path.parent.parent != _object_root(workspace) or digest in references:
        return None
    try:
        status = path.lstat()
    except OSError:
        return None
    is_file = (status.st_mode & 0o170000) == 0o100000
    if not is_file or now - status.st_mtime < MIN_AGE_SECONDS:
        return None
    return ObjectEntry(digest=digest, size=status.st_size, modified=int(status.st_mtime))


def _stored_object_paths(workspace: Path, problems: list[str]) -> Iterator[Path]:
    root = _object_root(workspace)
    try:
        fans = sorted(os.scandir(root), key=lambda item: item.name)
    except FileNotFoundError:
        return
    except OSError as error:
        problems.append(f"object store could not be listed: {error.strerror or error}")
        return
    for fan in fans:
        if _FAN_NAME.fullmatch(fan.name) is None or not fan.is_dir(follow_symlinks=False):
            continue
        try:
            names = sorted(item.name for item in os.scandir(fan.path))
        except OSError as error:
            problems.append(
                f"object folder {fan.name} could not be listed: {error.strerror or error}"
            )
            continue
        for name in names:
            if _OBJECT_NAME.fullmatch(name) is not None and name[:2] == fan.name:
                yield root / fan.name / name


def _current_candidates(
    workspace: Path, references: frozenset[str], now: float, problems: list[str]
) -> list[ObjectEntry]:
    entries = (
        inspect_object(path, workspace, references, now)
        for path in _stored_object_paths(workspace, problems)
    )
    return [entry for entry in entries if entry is not None]


def render_list(candidates: list[ObjectEntry]) -> bytes:
    lines = (
        f"{entry.digest}\t{entry.size}\t"
        f"{datetime.fromtimestamp(entry.modified, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        for entry in sorted(candidates, key=lambda item: item.digest)
    )
    return "".join(lines).encode("ascii")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".tmp")
    staging.write_bytes(data)
    os.replace(staging, path)


def _list_path(workspace: Path, list_sha256: str) -> Path:
    return workspace / PRUNE_DIRECTORY / f"candidates-{list_sha256}.tsv"


@contextmanager
def _exclusive(workspace: Path) -> Generator[Path]:
    root = workspace.resolve()
    if not root.is_dir():
        raise ObjectPruneRefused(f"workspace folder not found: {workspace}")
    ownership = acquire_workspace_owner(root, command="thoth objects prune")
    try:
        yield root
    finally:
        ownership.release()


def preview_unreferenced_objects(
    workspace: Path, *, clock: Callable[[], float] = time.time
) -> PreviewReport:
    """List candidates and write the list; deletes nothing. Raises WorkspaceInUse if in use."""
    with _exclusive(workspace) as root:
        references, problems = collect_references(root)
        problem_list = list(problems)
        candidates = (
            [] if problem_list else _current_candidates(root, references, clock(), problem_list)
        )
        if problem_list or not candidates:
            return PreviewReport(tuple(problem_list), (), 0, None, None)
        content = render_list(candidates)
        list_sha256 = hashlib.sha256(content).hexdigest()
        path = _list_path(root, list_sha256)
        try:
            _write_atomic(path, content)
        except OSError as error:
            problem_list.append(f"candidate list could not be written: {error.strerror or error}")
            return PreviewReport(tuple(problem_list), (), 0, None, None)
        total = sum(entry.size for entry in candidates)
        return PreviewReport((), tuple(candidates), total, list_sha256, path)


def _load_previewed_digests(root: Path, confirm: str) -> list[str]:
    if re.fullmatch(r"[0-9a-f]{64}", confirm) is None:
        raise ObjectPruneRefused("the confirmation value must be a 64-character SHA-256 hex string")
    path = _list_path(root, confirm)
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ObjectPruneRefused(
            "no preview list matches this hash; run the preview again"
        ) from error
    if hashlib.sha256(content).hexdigest() != confirm:
        raise ObjectPruneRefused(
            "the preview list changed after it was written; run the preview again"
        )
    digests = [line.split("\t", 1)[0] for line in content.decode("ascii").splitlines()]
    if any(_OBJECT_NAME.fullmatch(digest) is None for digest in digests):
        raise ObjectPruneRefused("the preview list is malformed; run the preview again")
    return digests


def _append_log(root: Path, report: DeleteReport) -> None:
    record = {
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "list_sha256": report.list_sha256,
        "deleted_count": report.deleted_count,
        "deleted_bytes": report.deleted_bytes,
        "skipped_count": report.skipped_count,
        "complete": not report.problems,
    }
    log = root / PRUNE_DIRECTORY / PRUNE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def delete_confirmed_objects(
    workspace: Path, confirm: str, *, clock: Callable[[], float] = time.time
) -> DeleteReport:
    """Delete previewed objects that are still unreferenced and old. Raises ObjectPruneRefused."""
    confirm = confirm.strip().lower()
    with _exclusive(workspace) as root:
        previewed = _load_previewed_digests(root, confirm)
        references, problems = collect_references(root)
        if problems:
            return DeleteReport(problems, 0, 0, len(previewed), confirm)
        report = _delete_each(root, previewed, references, clock, confirm)
        _append_log(root, report)
        return report


def _delete_each(
    root: Path,
    previewed: list[str],
    references: frozenset[str],
    clock: Callable[[], float],
    confirm: str,
) -> DeleteReport:
    deleted = skipped = freed = 0
    problems: list[str] = []
    for digest in previewed:
        path = _object_root(root) / digest[:2] / digest
        entry = inspect_object(path, root, references, clock())
        if entry is None:
            skipped += 1
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            skipped += 1
            continue
        except OSError as error:
            problems.append(f"object {digest} could not be deleted: {error.strerror or error}")
            break
        deleted += 1
        freed += entry.size
        with suppress(OSError):
            path.parent.rmdir()
    return DeleteReport(tuple(problems), deleted, freed, skipped, confirm)
