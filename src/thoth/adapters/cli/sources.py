"""Source staging and Git snapshot commands registered on an existing CLI app."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console


def register_source_commands(app: typer.Typer, console: Console, default_workspace: Path) -> None:
    DEFAULT_WORKSPACE = default_workspace

    def source_stage(
        source: Annotated[Path, typer.Option("--source", exists=True, dir_okay=False)],
        workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    ) -> None:
        """Copy one user-selected source into the bounded workspace inbox."""
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        inbox = workspace.resolve() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        destination = inbox / f"{digest[:16]}-{source.name}"
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise typer.BadParameter("existing staged path has different content")
        if not destination.exists():
            shutil.copy2(source, destination)
        console.print_json(
            json.dumps(
                {
                    "relative_path": destination.relative_to(inbox).as_posix(),
                    "byte_sha256": digest,
                    "bytes": len(raw),
                }
            )
        )

    app.command("source-stage")(source_stage)

    def git_snapshot(
        repository: Annotated[Path, typer.Option("--repository", exists=True, file_okay=False)],
        workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    ) -> None:
        """Create a read-only Git identity/status manifest in the workspace inbox."""
        resolved = repository.resolve()

        def git(*arguments: str) -> bytes:
            completed = subprocess.run(
                ["git", "-C", str(resolved), *arguments],
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                raise typer.BadParameter("Git metadata command failed")
            return completed.stdout

        top = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        head = git("rev-parse", "HEAD").decode().strip()
        status_records = [
            item for item in git("status", "--porcelain=v1", "-z").decode().split("\0") if item
        ]
        status_by_path = {
            record[3:]: record[:2]
            for record in status_records
            if len(record) >= 4 and " -> " not in record[3:]
        }
        entries: list[dict[str, object]] = []
        for record in git("ls-files", "--stage", "-z").decode().split("\0"):
            if not record:
                continue
            metadata_value, path = record.split("\t", 1)
            mode, object_id, stage = metadata_value.split(" ", 2)
            entries.append(
                {
                    "path": path,
                    "mode": mode,
                    "object_id": object_id,
                    "stage": int(stage),
                    "status": status_by_path.get(path),
                }
            )
        manifest: dict[str, object] = {
            "repository": top.name,
            "head": head,
            "dirty": bool(status_records),
            "entries": entries,
        }
        raw = orjson.dumps(manifest, option=orjson.OPT_SORT_KEYS)
        digest = hashlib.sha256(raw).hexdigest()
        inbox = workspace.resolve() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        destination = inbox / f"{digest[:16]}-{top.name}.git-manifest.json"
        if not destination.exists():
            destination.write_bytes(raw)
        console.print_json(
            json.dumps(
                {
                    "relative_path": destination.relative_to(inbox).as_posix(),
                    "byte_sha256": digest,
                    "head": head,
                    "dirty": bool(status_records),
                    "entry_count": len(entries),
                }
            )
        )

    app.command("git-snapshot")(git_snapshot)
