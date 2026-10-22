"""Real repair transactions defer infrastructure failures but preserve authority failures."""

from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.exc import OperationalError
from tests.atomicity.harness import snapshot
from tests.integration.journal_completion_helpers import prepare_gap
from tests.integration.storage_coverage_helpers import value

from thoth.adapters.storage.events import SqliteEventStore
from thoth.application.services.operation_journal import OperationJournal
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.event import EventRecord
from thoth.domain.operation import OperationRecord
from thoth.domain.resource_scope import ResourceScopeError
from thoth.protocol.journal_completion import JournalCompletion


async def test_operational_write_failure_rolls_back_and_preserves_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch)
    original = SqliteEventStore.insert_event
    try:
        before = snapshot(gap.runtime.ledger.engine)

        def failed(self: SqliteEventStore, event: EventRecord) -> None:
            original(self, event)
            if event.operation_id == gap.operation.operation_id:
                raise OperationalError("synthetic journal I/O", {}, OSError("synthetic"))

        with monkeypatch.context() as patch:
            patch.setattr(SqliteEventStore, "insert_event", failed)
            response = await gap.runtime.bus.dispatch(gap.command)
        assert value(response) == gap.operation.result
        assert snapshot(gap.runtime.ledger.engine) == before
        assert value(await gap.runtime.bus.dispatch(gap.command)) == gap.operation.result
        events = SqliteEventStore(gap.runtime.ledger.engine)
        assert len(events.list_events(gap.operation.operation_id)) == 3
        assert len(events.list_checkpoints(gap.operation.operation_id)) == 1
        assert gap.model.calls == 0
    finally:
        gap.runtime.close()


async def test_replay_authority_denial_happens_before_any_journal_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch)
    try:
        before = snapshot(gap.runtime.ledger.engine)

        def denied(self: ResourceScopeService, operation: OperationRecord) -> None:
            assert operation.operation_id == gap.operation.operation_id
            raise ResourceScopeError("RESOURCE_SCOPE_READ_DENIED")

        monkeypatch.setattr(ResourceScopeService, "require_operation", denied)
        response = await gap.runtime.bus.dispatch(gap.command)
        assert response.error is not None
        assert response.error.data["reason_code"] == "RESOURCE_SCOPE_READ_DENIED"
        assert snapshot(gap.runtime.ledger.engine) == before and gap.model.calls == 0
    finally:
        gap.runtime.close()


async def test_corrupt_result_binding_is_not_hidden_by_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch)
    try:
        with gap.runtime.ledger.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE operations SET result_json=? WHERE operation_id=?",
                ('{"tampered":true}', gap.operation.operation_id),
            )
        before = snapshot(gap.runtime.ledger.engine)
        response = await gap.runtime.bus.dispatch(gap.command)
        assert response.error is not None
        assert response.error.data["reason_code"] == "OPERATION_RESOURCE_BINDING_INVALID"
        assert snapshot(gap.runtime.ledger.engine) == before and gap.model.calls == 0
    finally:
        gap.runtime.close()


async def test_missing_workspace_pin_defers_without_acquiring_a_new_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch)
    try:
        completion = cast(JournalCompletion, vars(gap.runtime.bus)["_journal_completion"])
        monkeypatch.setattr(completion, "_pin", lambda: nullcontext(False))
        before = snapshot(gap.runtime.ledger.engine)
        assert completion.attempt(gap.operation).reason == "WORKSPACE_NOT_OWNED"
        assert value(await gap.runtime.bus.dispatch(gap.command)) == gap.operation.result
        assert snapshot(gap.runtime.ledger.engine) == before
    finally:
        gap.runtime.close()


async def test_hosted_replay_does_not_supplement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch, mode=DeploymentMode.HOSTED_REVIEW)
    try:
        assert vars(gap.runtime.bus)["_journal_completion"] is None
        before = snapshot(gap.runtime.ledger.engine)
        assert value(await gap.runtime.bus.dispatch(gap.command)) == gap.operation.result
        assert snapshot(gap.runtime.ledger.engine) == before and gap.model.calls == 0
    finally:
        gap.runtime.close()


async def test_sqlite_repair_rolls_back_clock_order_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = await prepare_gap(tmp_path, monkeypatch)
    try:
        store = SqliteEventStore(gap.runtime.ledger.engine)
        old_time = store.list_events(gap.operation.operation_id)[0].created_at - timedelta(
            seconds=1
        )

        class BackwardClock:
            def now(self) -> datetime:
                return old_time

        completion = cast(JournalCompletion, vars(gap.runtime.bus)["_journal_completion"])
        journal = cast(OperationJournal, vars(gap.runtime.bus)["_journal"])
        before = snapshot(gap.runtime.ledger.engine)
        with monkeypatch.context() as patch:
            patch.setattr(journal, "_clock", BackwardClock())
            outcome = completion.attempt(gap.operation)
        assert outcome.status == "DEFERRED" and outcome.reason == "UNSAFE_EVENT_CHAIN_OR_ORDER"
        assert snapshot(gap.runtime.ledger.engine) == before
        assert value(await gap.runtime.bus.dispatch(gap.command)) == gap.operation.result
        assert len(store.list_events(gap.operation.operation_id)) == 3
    finally:
        gap.runtime.close()
