"""Measured LOCAL success gaps are repaired once without rerunning their effects."""

from pathlib import Path

import pytest
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_operation_journal_failure_boundaries import NoModels

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.domain.event import Checkpoint, EventRecord
from thoth.protocol.bus import DispatchTicket

NOTIFICATIONS = {
    "project/metadata/update": ["project/metadata/updated"],
    "project/archive": ["project/archived", "project/statusChanged"],
    "project/restore": ["project/statusChanged"],
}


class InjectedTailFailure(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("method", "gap"),
    [
        *(
            ("project/metadata/update", gap)
            for gap in [
                "checkpoint-before",
                "checkpoint-after",
                "notification-before",
                "notification-after",
                "success-before",
                "success-after",
            ]
        ),
        ("project/archive", "notification-before"),
        ("project/archive", "second-notification-before"),
        ("project/archive", "second-notification-after"),
        ("project/restore", "notification-before"),
    ],
)
async def test_gap_repair_preserves_result_and_existing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, gap: str
) -> None:
    model = NoModels()
    runtime = create_runtime(tmp_path, model_resolver=model)
    command_fields: dict[str, object] = {"project_id": "project:j", "expected_revision": 0}
    try:
        value(
            await runtime.bus.dispatch(
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
        if method == "project/metadata/update":
            command_fields["name"] = "Saved once"
        if method == "project/restore":
            value(
                await runtime.bus.dispatch(
                    request("project/archive", "prepare-archive", command_fields)
                )
            )
            command_fields["expected_revision"] = 1
        command = request(method, "gap", command_fields)
        ticket = runtime.bus.claim(command)
        assert isinstance(ticket, DispatchTicket)
        identifier = ticket.operation.operation_id
        checkpoint_insert = SqliteEventStore.insert_checkpoint
        event_insert = SqliteEventStore.insert_event
        hits: list[str] = []

        def checkpoint(self: SqliteEventStore, point: Checkpoint) -> None:
            hit = point.operation_id == identifier and gap.startswith("checkpoint-")
            if hit and gap.endswith("before"):
                hits.append(gap)
                raise InjectedTailFailure(gap)
            checkpoint_insert(self, point)
            if hit:
                hits.append(gap)
                raise InjectedTailFailure(gap)

        def event(self: SqliteEventStore, item: EventRecord) -> None:
            index = 1 if gap.startswith("second-notification-") else 0
            selected = (
                "operation.succeeded"
                if gap.startswith("success-")
                else NOTIFICATIONS[method][index]
            )
            hit = (
                item.operation_id == identifier
                and item.event_type == selected
                and not gap.startswith("checkpoint-")
            )
            if hit and gap.endswith("before"):
                hits.append(gap)
                raise InjectedTailFailure(gap)
            event_insert(self, item)
            if hit:
                hits.append(gap)
                raise InjectedTailFailure(gap)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteEventStore, "insert_checkpoint", checkpoint)
            patch.setattr(SqliteEventStore, "insert_event", event)
            with pytest.raises(InjectedTailFailure):
                await runtime.bus.execute(ticket)
        assert hits == [gap]
        store = SqliteOperationStore(runtime.ledger.engine)
        original = store.read(identifier)
        assert (
            original is not None
            and original.result is not None
            and original.completed_at is not None
        )
        original_bytes = original.model_dump_json()
        journal = SqliteEventStore(runtime.ledger.engine)
        old_events = {
            item.event_id: item.model_dump_json() for item in journal.list_events(identifier)
        }
        old_points = {
            item.checkpoint_id: item.model_dump_json()
            for item in journal.list_checkpoints(identifier)
        }
        before = snapshot(runtime.ledger.engine)
        repaired = await runtime.bus.dispatch(command)
        assert value(repaired) == original.result
        stored = store.read(identifier)
        assert stored is not None and stored.model_dump_json() == original_bytes
        after = snapshot(runtime.ledger.engine)
        assert all(
            before[name] == after[name] for name in before if name not in {"events", "checkpoints"}
        )
        events = journal.list_events(identifier)
        assert [item.event_type for item in events] == [
            "operation.started",
            *NOTIFICATIONS[method],
            "operation.succeeded",
        ]
        points = journal.list_checkpoints(identifier)
        assert len(points) == 1
        for item in events:
            if item.event_id in old_events:
                assert item.model_dump_json() == old_events[item.event_id]
            else:
                assert item.payload["supplemented"] is True
                assert item.payload["supplemented_for"] == original.completed_at.isoformat()
        for point in points:
            if point.checkpoint_id in old_points:
                assert point.model_dump_json() == old_points[point.checkpoint_id]
            else:
                assert point.payload["supplemented"] is True
                assert point.payload["supplemented_for"] == original.completed_at.isoformat()
        assert await runtime.bus.dispatch(command) == repaired
        assert snapshot(runtime.ledger.engine) == after
        runtime.close()
        runtime = create_runtime(tmp_path, model_resolver=model)
        assert await runtime.bus.dispatch(command) == repaired
        assert snapshot(runtime.ledger.engine) == after
        assert model.calls == 0
    finally:
        runtime.close()
