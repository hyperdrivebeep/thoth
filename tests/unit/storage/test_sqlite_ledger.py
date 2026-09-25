from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from thoth.adapters.storage import SqliteLedger, migrate_sqlite_database
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.revision import EntitySnapshot


def test_initialize_and_project_scoped_fts(tmp_path: Path) -> None:
    migrate_sqlite_database(tmp_path / "ledger.sqlite3")
    ledger = SqliteLedger(tmp_path / "ledger.sqlite3")
    ledger.initialize()
    ledger.index_text("project:1", "node:1", "artifact:1", "야간 강우 시험 로그 누락")
    ledger.index_text("project:2", "node:2", "artifact:2", "야간 강우 다른 과제")

    project_one = tuple(ledger.search_text("project:1", "야간"))
    project_two = tuple(ledger.search_text("project:2", "야간"))

    assert project_one == (("node:1", "야간 강우 시험 로그 누락"),)
    assert project_two == (("node:2", "야간 강우 다른 과제"),)
    ledger.close()


def test_delete_indexed_text_removes_projection_and_source_row(tmp_path: Path) -> None:
    migrate_sqlite_database(tmp_path / "ledger.sqlite3")
    ledger = SqliteLedger(tmp_path / "ledger.sqlite3")
    ledger.initialize()
    ledger.index_text("project:1", "node:1", "artifact:1", "센서 교정 기록")
    ledger.delete_indexed_text("node:1")
    assert tuple(ledger.search_text("project:1", "센서")) == ()
    ledger.close()


def test_snapshot_persists_decimal_through_canonical_json(tmp_path: Path) -> None:
    migrate_sqlite_database(tmp_path / "ledger.sqlite3")
    ledger = SqliteLedger(tmp_path / "ledger.sqlite3")
    ledger.initialize()
    content: dict[str, object] = {"estimated_cost": Decimal("12.50")}
    snapshot = EntitySnapshot(
        snapshot_id="snapshot:decimal",
        project_id="project:1",
        entity_type=EntityType.ACTION,
        entity_id="action:1",
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )

    with ledger.transaction() as transaction:
        transaction.insert_snapshot(snapshot)

    restored = ledger.read_snapshot(snapshot.snapshot_id)
    assert restored is not None
    assert restored.content == {"estimated_cost": "12.50"}
    ledger.close()
