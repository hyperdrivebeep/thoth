"""The current schema migration factory retains its exact source binding."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.extension_binding_contract import SourceIndex, binding_errors

ROOT = Path(__file__).resolve().parents[2]


def test_current_schema_migration_class_binding_is_valid() -> None:
    manifest = json.loads((ROOT / "config/architecture-conformance.json").read_bytes())
    item = next(row for row in manifest["extension_points"] if row["name"] == "SCHEMA_MIGRATION")
    assert item["consumer_targets"] == ["thoth.adapters.storage.bundle.SqliteStoreFactory"]
    assert binding_errors(SourceIndex(ROOT), "SCHEMA_MIGRATION", item) == []
