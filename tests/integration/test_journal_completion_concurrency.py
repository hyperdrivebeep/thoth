"""Real SQLite callers coordinate normal tails and repeated repairs across runtimes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from tests.integration.journal_completion_helpers import TailInterrupted, prepare_gap
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_operation_journal_failure_boundaries import NoModels

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.workspace_owner import workspace_owner_key
from thoth.domain.event import Checkpoint
from thoth.protocol import journal_completion_gate as gates
from thoth.protocol.bus import DispatchTicket


async def test_two_runtime_replays_add_only_one_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch)
    second = create_runtime(tmp_path, model_resolver=gap.model)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(asyncio.run, gap.runtime.bus.dispatch(gap.command))
            b = pool.submit(asyncio.run, second.bus.dispatch(gap.command))
            left, right = a.result(timeout=10), b.result(timeout=10)
        assert left == right and value(left) == gap.operation.result
        events = SqliteEventStore(gap.runtime.ledger.engine)
        assert [e.event_type for e in events.list_events(gap.operation.operation_id)] == [
            "operation.started",
            "project/metadata/updated",
            "operation.succeeded",
        ]
        assert len(events.list_checkpoints(gap.operation.operation_id)) == 1
        assert gap.model.calls == 0
        assert (workspace_owner_key(tmp_path), gap.operation.operation_id) not in vars(gates)[
            "_entries"
        ]
    finally:
        second.close()
        gap.runtime.close()


@pytest.mark.parametrize("normal_fails", [False, True])
async def test_replay_waits_for_original_tail_before_deciding_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, normal_fails: bool
) -> None:
    model = NoModels()
    first = create_runtime(tmp_path, model_resolver=model)
    second = create_runtime(tmp_path, model_resolver=model)
    waiting, release = Event(), Event()
    try:
        value(
            await first.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:j",
                        "name": "Original",
                        "cutoff_at": "2026-10-01T00:00:00Z",
                    },
                )
            )
        )
        command = request(
            "project/metadata/update",
            "race",
            {
                "project_id": "project:j",
                "expected_revision": 0,
                "name": "Saved once",
            },
        )
        ticket = first.bus.claim(command)
        assert isinstance(ticket, DispatchTicket)
        identifier = ticket.operation.operation_id
        original = SqliteEventStore.insert_checkpoint

        def paused(self: SqliteEventStore, point: Checkpoint) -> None:
            if point.operation_id == identifier and self._engine is first.ledger.engine:  # pyright: ignore[reportPrivateUsage]
                waiting.set()
                assert release.wait(timeout=10)
                if normal_fails:
                    raise TailInterrupted("original tail interrupted")
            original(self, point)

        monkeypatch.setattr(SqliteEventStore, "insert_checkpoint", paused)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(asyncio.run, first.bus.execute(ticket))
            try:
                assert waiting.wait(timeout=10)
                replay = pool.submit(asyncio.run, second.bus.dispatch(command))
                key = (workspace_owner_key(tmp_path), identifier)
                deadline = monotonic() + 10
                while vars(gates)["_entries"][key].users < 2 and monotonic() < deadline:
                    Event().wait(0.005)
                assert vars(gates)["_entries"][key].users == 2
                assert not replay.done()
                release.set()
                if normal_fails:
                    with pytest.raises(TailInterrupted):
                        writer.result(timeout=10)
                else:
                    assert writer.result(timeout=10) == replay.result(timeout=10)
                assert value(replay.result(timeout=10))["revision"] == 1
            finally:
                release.set()
        store = SqliteEventStore(second.ledger.engine)
        events = store.list_events(identifier)
        assert [e.event_type for e in events] == [
            "operation.started",
            "project/metadata/updated",
            "operation.succeeded",
        ]
        assert len(store.list_checkpoints(identifier)) == 1
        assert all((e.payload.get("supplemented") is True) is normal_fails for e in events[1:])
        assert key not in vars(gates)["_entries"] and model.calls == 0
    finally:
        release.set()
        second.close()
        first.close()
