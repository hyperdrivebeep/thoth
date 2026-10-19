"""Internal ownership is atomic with claim and absent from every public view."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import Any

import httpx
import pytest
from sqlalchemy import Connection, create_engine, event

from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteLedger, migrate_sqlite_database
from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.operations import IdempotencyConflict, SqliteOperationStore
from thoth.application.commands.operations import OperationCommandHandlers
from thoth.application.services.operation_journal import OperationJournal
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationOwnerRecord, OperationRecord
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry


def owner(token: str | None = "b" * 64) -> OperationOwnerRecord:
    return OperationOwnerRecord(
        worker_id="worker:synthetic",
        process_id=987654,
        process_marker="synthetic-marker",
        host_token=token,
    )


def candidate(ownership: OperationOwnerRecord | None = None) -> OperationRecord:
    return OperationRecord(
        operation_id="operation:owner",
        project_id="project:owner",
        method="project/create",
        idempotency_key="owner-key",
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=datetime(2026, 10, 19, tzinfo=UTC),
        owner=ownership,
    )


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SqliteLedger]:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.delenv("THOTH_ALEMBIC_URL", raising=False)
    database = tmp_path / "test.sqlite3"
    migrate_sqlite_database(database)
    instance = SqliteLedger(database)
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


def counts(ledger: SqliteLedger) -> dict[str, int]:
    with ledger.engine.connect() as connection:
        return {
            name: connection.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar_one()
            for name in ("operations", "idempotency_keys", "operation_owners", "events")
        }


def test_owner_survives_read_terminal_and_duplicate_claim(ledger: SqliteLedger) -> None:
    store = SqliteOperationStore(ledger.engine)
    original = candidate(owner())
    journal = OperationJournal(SqliteEventStore(ledger.engine), SystemClock(), UuidIdGenerator())
    stored, replayed = store.claim(
        original, started_event=lambda: journal.prepare_started(original)
    )
    assert stored == original and not replayed
    before = counts(ledger)
    assert list(before.values()) == [1, 1, 1, 1]
    replacement = original.model_copy(update={"owner": owner("c" * 64)})
    replay, replayed = store.claim(replacement)
    assert replayed and replay.owner == original.owner and counts(ledger) == before
    with pytest.raises(IdempotencyConflict) as conflict:
        store.claim(replacement.model_copy(update={"scope_digest": "d" * 64}))
    assert conflict.value.existing.owner == original.owner and counts(ledger) == before
    assert SqliteOperationStore(ledger.engine).read(original.operation_id) == original
    completed = store.complete(
        original.operation_id, {"done": True}, completed_at=original.created_at
    )
    assert completed.owner == original.owner
    assert store.read(original.operation_id) == completed
    assert store.list_by_project(original.project_id)[0].owner is None


def test_competing_claims_keep_one_owner_and_reopen_it(ledger: SqliteLedger) -> None:
    barrier = Barrier(2)
    calls: list[str] = []

    def claim(token: str) -> tuple[OperationRecord, bool]:
        engine = create_engine(ledger.engine.url)
        original = candidate(owner(token * 64)).model_copy(
            update={"operation_id": "operation:" + token}
        )
        journal = OperationJournal(SqliteEventStore(engine), SystemClock(), UuidIdGenerator())

        def started():
            calls.append(token)
            return journal.prepare_started(original)

        try:
            barrier.wait(timeout=5)
            return SqliteOperationStore(engine).claim(original, started_event=started)
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("b", "c")))
    assert sorted(replayed for _, replayed in results) == [False, True]
    assert results[0][0] == results[1][0] and len(calls) == 1
    assert set(counts(ledger).values()) == {1}
    reopened = create_engine(ledger.engine.url)
    try:
        assert SqliteOperationStore(reopened).read(results[0][0].operation_id) == results[0][0]
    finally:
        reopened.dispose()


@pytest.mark.parametrize("ownership", [None, owner(None)])
def test_unknown_host_never_records_an_owner(
    ledger: SqliteLedger, ownership: OperationOwnerRecord | None
) -> None:
    store = SqliteOperationStore(ledger.engine)
    stored, replayed = store.claim(candidate(ownership))
    assert not replayed and stored.owner is None
    assert counts(ledger)["operation_owners"] == 0
    assert store.read(stored.operation_id) == stored


@pytest.mark.parametrize("fault", ["owner_insert", "event_factory", "event_insert"])
def test_owner_or_started_failure_rolls_back_the_entire_claim(
    ledger: SqliteLedger, fault: str
) -> None:
    store = SqliteOperationStore(ledger.engine)
    original = candidate(owner())
    journal = OperationJournal(SqliteEventStore(ledger.engine), SystemClock(), UuidIdGenerator())
    calls: list[str] = []

    def inserted(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        del connection, cursor, parameters, context, many
        if statement.startswith("INSERT INTO "):
            table = statement.split()[2]
            calls.append(table)
            if (fault == "owner_insert" and table == "operation_owners") or (
                fault == "event_insert" and table == "events"
            ):
                raise RuntimeError("injected owner boundary failure")

    def started():
        calls.append("started_factory")
        if fault == "event_factory":
            raise RuntimeError("injected owner boundary failure")
        return journal.prepare_started(original)

    event.listen(ledger.engine, "after_cursor_execute", inserted)
    try:
        with pytest.raises(RuntimeError, match="injected owner boundary failure"):
            store.claim(original, started_event=started)
        assert calls[:3] == ["operations", "idempotency_keys", "operation_owners"]
        assert ("started_factory" in calls) is (fault != "owner_insert")
        assert set(counts(ledger).values()) == {0}
    finally:
        event.remove(ledger.engine, "after_cursor_execute", inserted)
    assert (
        store.claim(original, started_event=lambda: journal.prepare_started(original))[1] is False
    )
    assert set(counts(ledger).values()) == {1}


async def test_get_rpc_and_events_keep_the_previous_public_payload(ledger: SqliteLedger) -> None:
    store = SqliteOperationStore(ledger.engine)
    original = candidate(owner())
    clock, ids = SystemClock(), UuidIdGenerator()
    events = SqliteEventStore(ledger.engine)
    journal = OperationJournal(events, clock, ids)
    assert journal.prepare_started(original).payload == {"method": original.method}
    store.claim(original, started_event=lambda: journal.prepare_started(original))
    registry = MethodRegistry()
    registry.register("operation/read", OperationCommandHandlers(store, clock=clock).read)
    bus = CommandBus(registry, store, clock, ids)
    application = create_app(bus)
    baseline = candidate().model_dump(mode="json")
    assert original.model_dump(mode="json") == baseline
    assert original.model_dump_json() == candidate().model_dump_json()
    assert "owner" not in OperationRecord.model_json_schema(mode="serialization")["properties"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1:8765"
    ) as client:
        response = await client.get(
            f"/operations/{original.operation_id}", params={"project_id": original.project_id}
        )
        assert response.status_code == 200 and response.json() == baseline
        response = await client.post(
            "/rpc",
            json={
                "id": "read",
                "method": "operation/read",
                "params": {
                    "_meta": {"idempotencyKey": "read"},
                    "input": {
                        "project_id": original.project_id,
                        "operation_id": original.operation_id,
                    },
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["value"] == baseline
    wire = " ".join(item.model_dump_json() for item in events.list_events(original.operation_id))
    for secret in ("synthetic-marker", "worker:synthetic", "987654", "b" * 64):
        assert secret not in wire
    assert events.list_events(original.operation_id)[0].payload == {"method": original.method}
