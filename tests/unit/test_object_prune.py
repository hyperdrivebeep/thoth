"""Object pruning: what survives, what goes, and every way the run refuses to delete."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from thoth.adapters.storage.object_prune import (
    DATABASE_RELATIVE,
    MIN_AGE_SECONDS,
    ObjectPruneRefused,
    delete_confirmed_objects,
    digests_in,
    preview_unreferenced_objects,
)
from thoth.adapters.storage.objects import ContentAddressedObjectStore
from thoth.apps.object_prune_commands import register_object_prune_commands

OLD = time.time() - 3 * MIN_AGE_SECONDS


def _database(workspace: Path) -> sqlite3.Connection:
    path = workspace / DATABASE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS artifacts (id TEXT, byte_sha256 TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT, content_json TEXT)")
    connection.commit()
    return connection


def _object(workspace: Path, text: str, *, modified: float | None = OLD) -> str:
    raw = text.encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = ContentAddressedObjectStore(workspace).put(raw, digest, operation_id="test")
    if modified is not None:
        os.utime(path, (modified, modified))
    return digest


def _path(workspace: Path, digest: str) -> Path:
    return workspace / "objects" / "sha256" / digest[:2] / digest


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    _database(workspace).close()
    return workspace


def test_references_in_row_json_and_ledger_files_survive_and_orphans_go(tmp_path: Path):
    workspace = _workspace(tmp_path)
    by_row = _object(workspace, "row")
    by_json = _object(workspace, "json")
    by_ledger = _object(workspace, "ledger")
    by_export = _object(workspace, "export")
    orphan = _object(workspace, "orphan")
    connection = _database(workspace)
    connection.execute("INSERT INTO artifacts VALUES ('a', ?)", (by_row,))
    receipt = {"receipt": {"output_blob_digest": by_json.upper()}}
    connection.execute("INSERT INTO runs VALUES ('r', ?)", (json.dumps(receipt),))
    connection.commit()
    connection.close()
    ledger = workspace / "diagnostics" / "internal-failures.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"digest": by_ledger}) + "\n", encoding="utf-8")
    bundle = workspace / "exports" / "bundle-1" / "canonical.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b'{"x": "' + by_export.encode() + b'"}')

    preview = preview_unreferenced_objects(workspace)
    assert preview.complete and [entry.digest for entry in preview.candidates] == [orphan]
    report = delete_confirmed_objects(workspace, preview.list_sha256 or "")

    assert (report.deleted_count, report.skipped_count, report.problems) == (1, 0, ())
    assert not _path(workspace, orphan).exists()
    assert not _path(workspace, orphan).parent.exists()
    for kept in (by_row, by_json, by_ledger, by_export):
        assert _path(workspace, kept).is_file()


def test_preview_deletes_nothing_and_its_list_hash_is_stable(tmp_path: Path):
    workspace = _workspace(tmp_path)
    orphan = _object(workspace, "orphan")
    first = preview_unreferenced_objects(workspace)
    second = preview_unreferenced_objects(workspace)
    assert _path(workspace, orphan).is_file()
    assert first.list_path is not None and first.list_sha256 is not None
    assert hashlib.sha256(first.list_path.read_bytes()).hexdigest() == first.list_sha256
    assert first.list_path.read_text(encoding="ascii").startswith(f"{orphan}\t")
    assert second.list_sha256 == first.list_sha256, "the tool's own list must not protect objects"


def test_wrong_or_altered_confirmation_is_refused_and_deletes_nothing(tmp_path: Path):
    workspace = _workspace(tmp_path)
    orphan = _object(workspace, "orphan")
    preview = preview_unreferenced_objects(workspace)
    for confirm in ("0" * 64, "not-a-hash", ""):
        with pytest.raises(ObjectPruneRefused):
            delete_confirmed_objects(workspace, confirm)
    assert preview.list_path is not None and preview.list_sha256 is not None
    preview.list_path.write_bytes(b"f" * 64 + b"\t1\t2026-01-01T00:00:00Z\n")
    with pytest.raises(ObjectPruneRefused, match="changed"):
        delete_confirmed_objects(workspace, preview.list_sha256)
    assert _path(workspace, orphan).is_file()
    assert not (workspace / "diagnostics" / "object-prune" / "prune-log.jsonl").exists()


def test_objects_younger_than_a_day_are_not_candidates(tmp_path: Path):
    workspace = _workspace(tmp_path)
    fresh = _object(workspace, "fresh", modified=None)
    almost = _object(workspace, "almost", modified=time.time() - MIN_AGE_SECONDS + 600)
    old = _object(workspace, "old")
    preview = preview_unreferenced_objects(workspace)
    assert [entry.digest for entry in preview.candidates] == [old]
    delete_confirmed_objects(workspace, preview.list_sha256 or "")
    assert _path(workspace, fresh).is_file() and _path(workspace, almost).is_file()


def test_only_exactly_shaped_object_files_are_candidates(tmp_path: Path):
    workspace = _workspace(tmp_path)
    digest = _object(workspace, "real")
    root = workspace / "objects" / "sha256"
    stray = {
        root / "zz" / ("a" * 64): b"bad folder",
        root / "ab" / ("c" * 64): b"folder does not match name",
        root / "ab" / ("D" * 64): b"upper-case name",
        root / "ab" / "note.txt": b"not an object",
        root / "loose-file": b"not in a folder",
        root.parent / "other" / ("e" * 64): b"outside the store",
    }
    for path, content in stray.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (OLD, OLD))
    preview = preview_unreferenced_objects(workspace)
    assert [entry.digest for entry in preview.candidates] == [digest]
    delete_confirmed_objects(workspace, preview.list_sha256 or "")
    assert all(path.is_file() for path in stray)


def test_object_referenced_or_touched_after_the_preview_is_skipped(tmp_path: Path):
    workspace = _workspace(tmp_path)
    claimed = _object(workspace, "claimed later")
    touched = _object(workspace, "touched later")
    gone = _object(workspace, "orphan")
    preview = preview_unreferenced_objects(workspace)
    connection = _database(workspace)
    connection.execute("INSERT INTO artifacts VALUES ('a', ?)", (claimed,))
    connection.commit()
    connection.close()
    now = time.time()
    os.utime(_path(workspace, touched), (now, now))
    report = delete_confirmed_objects(workspace, preview.list_sha256 or "")
    assert (report.deleted_count, report.skipped_count) == (1, 2)
    assert _path(workspace, claimed).is_file() and _path(workspace, touched).is_file()
    assert not _path(workspace, gone).exists()


def test_unreadable_database_stops_everything_without_a_list_hash(tmp_path: Path):
    workspace = _workspace(tmp_path)
    orphan = _object(workspace, "orphan")
    preview = preview_unreferenced_objects(workspace)
    assert preview.complete
    (workspace / DATABASE_RELATIVE).write_bytes(b"this is not a database" * 100)
    broken = preview_unreferenced_objects(workspace)
    assert not broken.complete and broken.list_sha256 is None and broken.list_path is None
    deleted = delete_confirmed_objects(workspace, preview.list_sha256 or "")
    assert deleted.problems and deleted.deleted_count == 0
    assert _path(workspace, orphan).is_file()


def test_missing_database_is_incomplete(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _object(workspace, "orphan")
    preview = preview_unreferenced_objects(workspace)
    assert not preview.complete and preview.list_sha256 is None
    with pytest.raises(ObjectPruneRefused, match="not found"):
        preview_unreferenced_objects(tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_unreadable_workspace_file_stops_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = _workspace(tmp_path)
    orphan = _object(workspace, "orphan")
    ledger = workspace / "diagnostics" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{}", encoding="utf-8")
    original: Any = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "ledger.jsonl":
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    preview = preview_unreferenced_objects(workspace)
    assert not preview.complete and preview.list_sha256 is None
    assert any("ledger.jsonl" in problem for problem in preview.problems)
    assert _path(workspace, orphan).is_file()


def test_digest_scan_handles_case_and_long_hex():
    digest = "ab" * 32
    assert digests_in(digest.upper().encode()) == {digest}
    assert digest in digests_in(b"0" + digest.encode() + b"f")
    assert digests_in(b"abc") == set()


def _cli(default: Path) -> typer.Typer:
    app = typer.Typer()
    register_object_prune_commands(app, default)
    return app


def test_command_previews_then_deletes_with_the_printed_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("THOTH_DEPLOYMENT_MODE", raising=False)
    workspace = _workspace(tmp_path)
    orphan = _object(workspace, "orphan")
    runner = CliRunner()
    app = _cli(workspace)
    shown = runner.invoke(app, ["objects", "prune"])
    assert shown.exit_code == 0, shown.output
    assert "Unreferenced objects older than 24 hours: 1" in shown.output
    assert _path(workspace, orphan).is_file()
    printed = next(
        line.split(": ", 1)[1] for line in shown.output.splitlines() if "List SHA-256" in line
    )
    refused = runner.invoke(app, ["objects", "prune", "--confirm", "1" * 64])
    assert refused.exit_code == 1 and "Refused" in refused.output
    assert _path(workspace, orphan).is_file()
    done = runner.invoke(app, ["objects", "prune", "--confirm", printed])
    assert done.exit_code == 0 and "Deleted 1 objects" in done.output
    assert not _path(workspace, orphan).exists()
    record = json.loads(
        (workspace / "diagnostics" / "object-prune" / "prune-log.jsonl").read_text().splitlines()[0]
    )
    assert record["list_sha256"] == printed
    assert (record["deleted_count"], record["deleted_bytes"]) == (1, len(b"orphan"))
    again = runner.invoke(app, ["objects", "prune"])
    assert again.exit_code == 0 and "Nothing to delete" in again.output


def test_command_reports_an_incomplete_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("THOTH_DEPLOYMENT_MODE", raising=False)
    workspace = _workspace(tmp_path)
    _object(workspace, "orphan")
    (workspace / DATABASE_RELATIVE).write_bytes(b"garbage" * 200)
    result = CliRunner().invoke(_cli(workspace), ["objects", "prune"])
    assert result.exit_code == 1
    assert "Preview incomplete" in result.output and "List SHA-256" not in result.output
