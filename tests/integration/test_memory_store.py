from __future__ import annotations

from pathlib import Path

from thoth.adapters.storage import SqliteLedger, SqliteMemoryStore, migrate_sqlite_database
from thoth.domain.enums import MemoryKind, MemoryPayloadMode, RecallEligibility
from thoth.domain.memory import MemoryRecord


def test_memory_store_preserves_project_scope_and_revision_binding(tmp_path: Path) -> None:
    migrate_sqlite_database(tmp_path / "memory.sqlite3")
    ledger = SqliteLedger(tmp_path / "memory.sqlite3")
    ledger.initialize()
    store = SqliteMemoryStore(ledger.engine)
    record = MemoryRecord(
        memory_id="memory:1",
        project_id="project:1",
        payload_mode=MemoryPayloadMode.DOMAIN_REFERENCE,
        kind=MemoryKind.FAILURE,
        owner_revision_ref="revision:1",
        source_ref="outcome:1",
        recall_eligibility=RecallEligibility.WORKING_CONTEXT,
        revision_digest="a" * 64,
    )
    store.add(record)

    assert store.list("project:1") == (record,)
    assert store.list("project:2") == ()
    ledger.close()
