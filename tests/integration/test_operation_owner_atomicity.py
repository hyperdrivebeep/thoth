"""Two sealers and injected durable-event failure use one terminal transaction."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_operation_owner_recovery import abandoned, terminal_counts

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.transaction import _AMBIENT  # pyright: ignore[reportPrivateUsage]
from thoth.adapters.worker_identity import LocalWorkerIdentity
from thoth.domain.event import EventRecord
from thoth.protocol.jsonrpc import JsonRpcRequest


async def test_two_sealers_publish_one_failure_and_one_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, command, identifier = await abandoned(tmp_path)
    second = create_runtime(tmp_path / "workspace")
    original = SqliteOperationStore(first.ledger.engine).read(identifier)
    assert original is not None and original.owner is not None
    stale_worker = original.owner.worker_id
    barrier = Barrier(2)
    alive = LocalWorkerIdentity.is_alive
    observations: list[bool | None] = []
    measurements: list[str] = []

    def observed(self: LocalWorkerIdentity, pid: int, marker: str, worker: str) -> bool | None:
        assert _AMBIENT.get() is None
        result = alive(self, pid, marker, worker)
        if worker == stale_worker:
            observations.append(result)
            barrier.wait(timeout=10)
        return result

    def measured(request: JsonRpcRequest, project: str, outcome: str) -> None:
        del request, project
        assert _AMBIENT.get() is None
        measurements.append(outcome)

    monkeypatch.setattr(LocalWorkerIdentity, "is_alive", observed)
    monkeypatch.setattr(first.bus, "_measure", measured)
    monkeypatch.setattr(second.bus, "_measure", measured)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(asyncio.run, first.bus.dispatch(command))
            b = executor.submit(asyncio.run, second.bus.dispatch(command))
            replies = [a.result(timeout=15), b.result(timeout=15)]
        assert observations == [False, False]
        assert replies[0] == replies[1] and replies[0].error is not None
        assert measurements == ["FAILED"] and terminal_counts(first, identifier) == (1, 1)
        stored = SqliteOperationStore(first.ledger.engine).read(identifier)
        assert stored is not None and stored.error == replies[0].error.model_dump(mode="json")
        assert stored.resource_uses is not None
    finally:
        second.close()
        first.close()


async def test_event_insert_failure_rolls_back_failure_binding_and_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, command, identifier = await abandoned(tmp_path)
    store = SqliteOperationStore(runtime.ledger.engine)
    events = SqliteEventStore(runtime.ledger.engine)
    before, before_events = store.read(identifier), events.list_events(identifier)
    measurements: list[str] = []
    insert = SqliteEventStore.insert_event

    def injected(self: SqliteEventStore, event: EventRecord) -> None:
        insert(self, event)
        if event.operation_id == identifier and event.event_type == "operation.failed":
            assert _AMBIENT.get() is not None
            raise RuntimeError("injected after durable failure event")

    def measured(request: JsonRpcRequest, project: str, outcome: str) -> None:
        del request, project
        measurements.append(outcome)

    monkeypatch.setattr(runtime.bus, "_measure", measured)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteEventStore, "insert_event", injected)
            with pytest.raises(RuntimeError, match="injected after durable failure event"):
                await runtime.bus.dispatch(command)
        assert store.read(identifier) == before
        assert events.list_events(identifier) == before_events
        assert terminal_counts(runtime, identifier) == (0, 0) and measurements == []
        reply = await runtime.bus.dispatch(command)
        assert reply.error is not None and reply.error.data["reason_code"] == "OPERATION_OWNER_GONE"
        assert terminal_counts(runtime, identifier) == (1, 1) and measurements == ["FAILED"]
    finally:
        runtime.close()
