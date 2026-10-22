"""Real RPC failures at either append must publish neither memory control record."""

from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import snapshot
from tests.integration.test_m4_memory_control_multiwrite_observation import (
    EXPECTED_ORDER,
    ROUTES,
    call,
    domain_changes,
    later_reads,
    prepare,
    request,
    rows,
    table_changes,
)

from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.services.control_record_service import ControlRecordService
from thoth.apps.runtime import create_runtime
from thoth.domain.control_record import ControlRecord
from thoth.protocol.deferred import current_operation


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("write_number", [1, 2])
@pytest.mark.parametrize("after_write", [False, True])
async def test_append_fault_rolls_back_pair_and_replay_keeps_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    write_number: int,
    after_write: bool,
) -> None:
    workspace = tmp_path / "workspace"
    runtime, method, payload, candidate_id = await prepare(workspace, route)
    key = f"p1-{route}-{write_number}-{after_write}"
    original = SqliteControlRecordStore.append
    hits: list[str] = []

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        operation = current_operation.get()
        if operation is None or operation.idempotency_key != key:
            original(store, record)
            return
        hits.append(record.record_type)
        if len(hits) == write_number:
            if after_write:
                original(store, record)
            raise RuntimeError("P1 injected append failure")
        original(store, record)

    try:
        reads_before = await later_reads(runtime, route, candidate_id, "p1-before")
        rows_before = rows(runtime)
        before = snapshot(runtime.ledger.engine)
        command = request(method, key, payload)
        with monkeypatch.context() as patch:
            patch.setattr(SqliteControlRecordStore, "append", append)
            failed = await runtime.bus.dispatch(command)
        assert failed.error is not None
        assert hits == [kind for kind, _ in EXPECTED_ORDER[route]][:write_number]
        after = snapshot(runtime.ledger.engine)
        assert rows(runtime) == rows_before
        assert domain_changes(table_changes(before, after), before, after, command) == {}
        assert await later_reads(runtime, route, candidate_id, "p1-after") == reads_before
        runtime.close()
        runtime = create_runtime(workspace)
        assert rows(runtime) == rows_before
        before_replay = snapshot(runtime.ledger.engine)
        replay = await runtime.bus.dispatch(command)
        assert replay.error == failed.error
        assert snapshot(runtime.ledger.engine) == before_replay
        await call(runtime, method, key + "-fresh", payload)
        added = rows(runtime)[len(rows_before) :]
        assert [row["type"] for row in added] == [kind for kind, _ in EXPECTED_ORDER[route]]
    finally:
        runtime.close()


async def test_bound_helper_looks_up_create_after_handler_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, method, payload, _ = await prepare(tmp_path / "workspace", "validate")
    key = "p1-create-monkeypatch-after-construction"
    original = ControlRecordService.create
    hits: list[str] = []

    def create(
        service: ControlRecordService,
        *,
        project_id: str,
        namespace: str,
        record_type: str,
        state: str,
        payload: dict[str, object],
        record_id: str | None = None,
    ) -> ControlRecord:
        operation = current_operation.get()
        if operation is not None and operation.idempotency_key == key:
            hits.append(record_type)
        return original(
            service,
            project_id=project_id,
            namespace=namespace,
            record_type=record_type,
            state=state,
            payload=payload,
            record_id=record_id,
        )

    try:
        monkeypatch.setattr(ControlRecordService, "create", create)
        await call(runtime, method, key, payload)
        assert hits == ["GATE", "CANDIDATE"]
    finally:
        runtime.close()


@pytest.mark.parametrize("route", ROUTES)
async def test_partial_first_append_is_not_visible_to_independent_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    runtime, method, payload, _ = await prepare(tmp_path / "workspace", route)
    key = f"p1-reader-{route}"
    original = SqliteControlRecordStore.append
    first_views: list[list[dict[str, Any]]] = []
    seen: list[str] = []
    before = rows(runtime)

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        original(store, record)
        operation = current_operation.get()
        if operation is not None and operation.idempotency_key == key:
            seen.append(record.record_type)
            if len(seen) == 1:
                # rows() deliberately opens another connection, bypassing ambient UoW.
                first_views.append(rows(runtime))

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteControlRecordStore, "append", append)
            await call(runtime, method, key, payload)
        assert seen == [kind for kind, _ in EXPECTED_ORDER[route]]
        assert first_views == [before]
        assert len(rows(runtime)) == len(before) + 2
    finally:
        runtime.close()
