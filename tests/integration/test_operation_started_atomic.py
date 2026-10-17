"""Claim/start-event atomicity, lazy clock use, and unchanged journal encoding."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, event, func, select
from sqlalchemy.exc import OperationalError
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_operation_journal_failure_boundaries import (
    NoModels,
    independent_snapshot,
)

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.schema import events, idempotency_keys, operations
from thoth.application.services.operation_journal import OperationJournal
from thoth.apps.runtime import AppRuntime
from thoth.domain.enums import OperationState
from thoth.domain.event import Checkpoint, EventRecord
from thoth.domain.operation import OperationRecord
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse, RpcErrorCode


class CountingClock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        result = datetime(2026, 10, 16, tzinfo=UTC) + timedelta(seconds=self.calls)
        self.calls += 1
        return result


class CountingIds:
    def __init__(self) -> None:
        self.prefixes: list[str] = []

    def new(self, prefix: str) -> str:
        self.prefixes.append(prefix)
        return f"{prefix}:{len(self.prefixes)}"


def command(name: str = "Updated once") -> JsonRpcRequest:
    return request(
        "project/metadata/update",
        "atomic-start-key",
        {"project_id": "project:atomic-start", "expected_revision": 0, "name": name},
    )


async def setup(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AppRuntime, CountingIds, CountingClock]:
    runtime = create_runtime(workspace, model_resolver=NoModels())
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "create-atomic-start",
                {
                    "project_id": "project:atomic-start",
                    "name": "Before",
                    "cutoff_at": "2026-09-05T00:00:00Z",
                },
            )
        )
    )
    ids, clock = CountingIds(), CountingClock()
    monkeypatch.setattr(
        runtime.bus,
        "_journal",
        OperationJournal(SqliteEventStore(runtime.ledger.engine), clock, ids),
    )
    return runtime, ids, clock


def target_counts(connection: Connection) -> list[int]:
    identifiers = select(operations.c.operation_id).where(
        operations.c.idempotency_key == "atomic-start-key"
    )
    return [
        connection.execute(
            select(func.count()).select_from(table).where(table.c.operation_id.in_(identifiers))
        ).scalar_one()
        for table in (operations, idempotency_keys, events)
    ]


async def test_replay_conflict_and_reopen_do_not_generate_started_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ids, clock = await setup(tmp_path, monkeypatch)
    try:
        ticket = runtime.bus.claim(command())
        assert isinstance(ticket, DispatchTicket)
        assert ids.prefixes == ["event"] and clock.calls == 1
        claimed = snapshot(runtime.ledger.engine)
        replay = runtime.bus.claim(command())
        conflict = runtime.bus.claim(command("Different payload"))
        assert isinstance(replay, JsonRpcResponse) and replay.result is not None
        assert replay.result["state"] == "RUNNING"
        assert isinstance(conflict, JsonRpcResponse) and conflict.error is not None
        assert conflict.error.code == RpcErrorCode.IDEMPOTENCY_CONFLICT
        assert snapshot(runtime.ledger.engine) == claimed
        assert ids.prefixes == ["event"] and clock.calls == 1
        result = await runtime.bus.execute(ticket)
        assert value(result)["revision"] == 1
        before_replay = snapshot(runtime.ledger.engine)
        before_counts = (list(ids.prefixes), clock.calls)
        assert (await runtime.bus.dispatch(command())).model_dump() == result.model_dump()
        assert (ids.prefixes, clock.calls) == before_counts
        assert snapshot(runtime.ledger.engine) == before_replay
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path, model_resolver=NoModels())
    try:
        assert snapshot(reopened.ledger.engine) == before_replay
        assert (await reopened.bus.dispatch(command())).model_dump() == result.model_dump()
        assert snapshot(reopened.ledger.engine) == before_replay
    finally:
        reopened.close()


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
@pytest.mark.parametrize("point", ["insert", "commit"])
async def test_started_write_or_commit_busy_rolls_back_all_three_and_retries_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
    code: int,
    point: str,
) -> None:
    runtime, ids, clock = await setup(tmp_path, monkeypatch)
    hits: list[list[int]] = []
    before = snapshot(runtime.ledger.engine)

    def fault(connection: Connection) -> None:
        staged = target_counts(connection)
        assert staged == [1, 1, 1]
        assert independent_snapshot(runtime) == before
        hits.append(staged)
        # Inject the native SQLite code at the real write/commit seam, not the bus.
        error = sqlite3.OperationalError("injected started write contention")
        error.sqlite_errorcode = code
        raise OperationalError("operation.started " + point, {}, error)

    def after_insert(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        del cursor, parameters, context, many
        if statement.startswith("INSERT INTO events"):
            fault(connection)

    name = "after_cursor_execute" if point == "insert" else "commit"
    listener = after_insert if point == "insert" else fault
    event.listen(runtime.ledger.engine, name, listener)
    try:
        try:
            failed = await runtime.bus.dispatch(command())
        finally:
            event.remove(runtime.ledger.engine, name, listener)
        assert hits == [[1, 1, 1]], "NOT_TRIGGERED or repeated started fault"
        assert failed.error is not None and failed.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert failed.error.data == {
            "reason_code": "OPERATION_CLAIM_BUSY",
            "operation_claimed": False,
            "retryable": True,
        }
        assert snapshot(runtime.ledger.engine) == before == independent_snapshot(runtime)
        # Post-generation BUSY consumes this ID/time; it is never rewound or reused.
        assert ids.prefixes == ["event"] and clock.calls == 1
        result = await runtime.bus.dispatch(command())
        assert value(result)["revision"] == 1
        assert result.result is not None
        identifier = str(result.result["operation_id"])
        chain = SqliteEventStore(runtime.ledger.engine).list_events(identifier)
        assert chain[0].event_type == "operation.started" and chain[0].event_id == "event:2"
        assert sum(item.event_type == "operation.started" for item in chain) == 1
        completed = snapshot(runtime.ledger.engine)
        assert (await runtime.bus.dispatch(command())).model_dump() == result.model_dump()
        assert snapshot(runtime.ledger.engine) == completed
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path, model_resolver=NoModels())
    try:
        assert snapshot(reopened.ledger.engine) == completed
        assert (await reopened.bus.dispatch(command())).model_dump() == result.model_dump()
        assert snapshot(reopened.ledger.engine) == completed
    finally:
        reopened.close()
    record_property(
        "atomic_started",
        json.dumps({"code": code, "point": point, "hits": hits, "retry_revision": 1}),
    )


async def test_busy_before_event_generation_uses_no_event_id_or_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ids, clock = await setup(tmp_path, monkeypatch)
    before = snapshot(runtime.ledger.engine)

    def short_wait(connection: sqlite3.Connection, record: Any, proxy: Any) -> None:
        del record, proxy
        connection.execute("PRAGMA busy_timeout=50").close()

    event.listen(runtime.ledger.engine, "checkout", short_wait)
    try:
        with runtime.ledger.engine.connect() as blocker:
            blocker.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                result = await runtime.bus.dispatch(command())
                assert result.error is not None
                assert result.error.data["reason_code"] == "OPERATION_CLAIM_BUSY"
                assert result.error.data["operation_claimed"] is False
                assert ids.prefixes == [] and clock.calls == 0
            finally:
                blocker.rollback()
        assert snapshot(runtime.ledger.engine) == before
        assert value(await runtime.bus.dispatch(command()))["revision"] == 1
    finally:
        event.remove(runtime.ledger.engine, "checkout", short_wait)
        runtime.close()


async def test_direct_claim_without_factory_keeps_existing_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ids, clock = await setup(tmp_path, monkeypatch)
    candidate = OperationRecord(
        operation_id="operation:direct",
        project_id="project:atomic-start",
        method="project/metadata/update",
        idempotency_key="direct-key",
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    try:
        store = SqliteOperationStore(runtime.ledger.engine)
        assert store.claim(candidate) == (candidate, False)
        assert store.claim(candidate) == (candidate, True)
        assert SqliteEventStore(runtime.ledger.engine).list_events(candidate.operation_id) == ()
        assert ids.prefixes == [] and clock.calls == 0
    finally:
        runtime.close()


async def test_new_claim_preserves_operation_then_event_id_clock_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, _ = await setup(tmp_path, monkeypatch)
    calls: list[str] = []

    class OrderedIds(CountingIds):
        def new(self, prefix: str) -> str:
            calls.append("id:" + prefix)
            return super().new(prefix)

    class OrderedClock(CountingClock):
        def now(self) -> datetime:
            calls.append("clock")
            return super().now()

    ids, clock = OrderedIds(), OrderedClock()
    monkeypatch.setattr(runtime.bus, "_ids", ids)
    monkeypatch.setattr(runtime.bus, "_clock", clock)
    monkeypatch.setattr(
        runtime.bus,
        "_journal",
        OperationJournal(SqliteEventStore(runtime.ledger.engine), clock, ids),
    )

    def inserted(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        del connection, cursor, parameters, context, many
        for table in ("operations", "idempotency_keys", "events"):
            if statement.startswith("INSERT INTO " + table):
                calls.append("insert:" + table)

    event.listen(runtime.ledger.engine, "after_cursor_execute", inserted)
    try:
        assert isinstance(runtime.bus.claim(command()), DispatchTicket)
        assert calls == [
            "id:operation",
            "clock",
            "insert:operations",
            "insert:idempotency_keys",
            "id:event",
            "clock",
            "insert:events",
        ]
    finally:
        event.remove(runtime.ledger.engine, "after_cursor_execute", inserted)
        runtime.close()


class EventSink:
    def __init__(self) -> None:
        self.events: list[EventRecord] = []
        self.checkpoints: list[Checkpoint] = []
        self.reads = 0

    def list_events(self, operation_id: str) -> tuple[EventRecord, ...]:
        self.reads += 1
        return tuple(item for item in self.events if item.operation_id == operation_id)

    def insert_event(self, event: EventRecord) -> None:
        self.events.append(event)

    def insert_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.checkpoints.append(checkpoint)

    def latest_checkpoint(self, operation_id: str) -> Checkpoint | None:
        return next(
            (item for item in reversed(self.checkpoints) if item.operation_id == operation_id), None
        )


def test_shared_event_factory_preserves_original_append_bytes_without_io() -> None:
    store = EventSink()
    journal = OperationJournal(store, CountingClock(), CountingIds())
    first = journal.append(
        project_id="project:golden",
        operation_id="operation:golden",
        event_type="operation.started",
        payload={"method": "project/metadata/update"},
    )
    second = journal.append(
        project_id="project:golden",
        operation_id="operation:golden",
        event_type="operation.succeeded",
        payload={"result_digest": "a" * 64},
    )
    checkpoint = journal.checkpoint(
        operation_id="operation:golden", payload={"state": "SUCCEEDED", "result_digest": "a" * 64}
    )
    frozen = Path(__file__).parents[1] / "fixtures" / "operation_journal_wire_105.json"
    assert [item.model_dump_json() for item in (first, second, checkpoint)] == json.loads(
        frozen.read_text(encoding="utf-8")
    )
    fresh = EventSink()
    factory = OperationJournal(fresh, CountingClock(), CountingIds())
    candidate = OperationRecord(
        operation_id="operation:golden",
        project_id="project:golden",
        method="project/metadata/update",
        idempotency_key="golden-key",
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert factory.prepare_started(candidate).model_dump_json() == first.model_dump_json()
    assert fresh.reads == 0 and fresh.events == [] and fresh.checkpoints == []
