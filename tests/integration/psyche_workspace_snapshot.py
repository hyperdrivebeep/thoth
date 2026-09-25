"""Clone a user workspace database for local regression tests without opening it writable."""

import sqlite3
from pathlib import Path


def clone_database_read_only(source_workspace: Path, destination_workspace: Path) -> Path:
    source = (source_workspace / "db" / "thoth.sqlite3").resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = destination_workspace / "db" / "thoth.sqlite3"
    if source == destination.resolve():
        raise ValueError("source and destination database must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)

    source_connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    try:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()
    return destination
