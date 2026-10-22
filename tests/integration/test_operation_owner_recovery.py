"""Normal LOCAL entry: dead workers seal, unknown/live/old rows remain untouched."""

from pathlib import Path
from typing import Any

import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.transaction import _AMBIENT  # pyright: ignore[reportPrivateUsage]
from thoth.apps.runtime import AppRuntime
from thoth.domain.enums import OperationState
from thoth.domain.resource_scope import ResourceScopeError
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcRequest


async def abandoned(tmp_path: Path) -> tuple[AppRuntime, JsonRpcRequest, str]:
    workspace = tmp_path / "workspace"
    first, project = await prepare_project(workspace)
    command = request(
        "project/metadata/update",
        "abandoned",
        {
            "project_id": project,
            "expected_revision": 0,
            "name": "Never executed",
        },
    )
    claimed = first.bus.claim(command)
    assert isinstance(claimed, DispatchTicket) and claimed.operation.owner is not None
    first.close()
    return create_runtime(workspace), command, claimed.operation.operation_id


def terminal_counts(runtime: AppRuntime, identifier: str) -> tuple[int, int]:
    with runtime.ledger.engine.connect() as connection:
        events = connection.exec_driver_sql(
            "SELECT count(*) FROM events WHERE operation_id=? AND event_type='operation.failed'",
            (identifier,),
        ).scalar_one()
        bindings = connection.exec_driver_sql(
            "SELECT count(*) FROM operation_resource_bindings WHERE operation_id=?",
            (identifier,),
        ).scalar_one()
    return events, bindings


async def test_closed_runtime_same_pid_seals_once_then_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, command, identifier = await abandoned(tmp_path)
    measurements: list[str] = []

    def measured(request: JsonRpcRequest, project: str, outcome: str) -> None:
        del request, project
        assert _AMBIENT.get() is None
        stored = SqliteOperationStore(runtime.ledger.engine).read(identifier)
        assert stored is not None and stored.state is OperationState.FAILED
        assert terminal_counts(runtime, identifier) == (1, 1)
        measurements.append(outcome)

    monkeypatch.setattr(runtime.bus, "_measure", measured)
    try:
        first = await runtime.bus.dispatch(command)
        second = await runtime.bus.dispatch(command)
        assert first == second and first.error is not None
        assert first.error.data["reason_code"] == "OPERATION_OWNER_GONE"
        assert first.error.data["remote_observation"] == "UNKNOWN"
        assert first.error.data["new_request_required"] is True
        assert measurements == ["FAILED"] and terminal_counts(runtime, identifier) == (1, 1)
        project = value(
            await runtime.bus.query(
                request(
                    "project/read",
                    "read",
                    {
                        "project_id": command.params.input["project_id"],
                    },
                )
            )
        )
        assert project["name"] == "Storage coverage" and project["revision"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ["owner-missing", "host-mismatch", "unknown-liveness"])
async def test_uncertain_or_old_owner_keeps_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    runtime, command, identifier = await abandoned(tmp_path)
    try:
        if case == "unknown-liveness":
            from thoth.adapters.worker_identity import LocalWorkerIdentity

            def unknown(*args: object) -> None:
                return None

            monkeypatch.setattr(LocalWorkerIdentity, "is_alive", unknown)
        else:
            with runtime.ledger.engine.begin() as connection:
                if case == "owner-missing":
                    connection.exec_driver_sql(
                        "DELETE FROM operation_owners WHERE operation_id=?", (identifier,)
                    )
                else:
                    connection.exec_driver_sql(
                        "UPDATE operation_owners SET host_token=? WHERE operation_id=?",
                        ("f" * 64, identifier),
                    )
        result = await runtime.bus.dispatch(command)
        assert result.error is None and result.result is not None
        assert result.result["state"] == "RUNNING" and terminal_counts(runtime, identifier) == (
            0,
            0,
        )
    finally:
        runtime.close()


async def test_live_owner_keeps_running(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path / "workspace")
    command = request(
        "project/metadata/update",
        "live",
        {"project_id": project, "expected_revision": 0, "name": "Later"},
    )
    try:
        claimed = runtime.bus.claim(command)
        assert isinstance(claimed, DispatchTicket)
        response = await runtime.bus.dispatch(command)
        assert response.error is None and response.result is not None
        assert response.result["state"] == "RUNNING"
        assert terminal_counts(runtime, claimed.operation.operation_id) == (0, 0)
        assert value(await runtime.bus.execute(claimed))["revision"] == 1
    finally:
        runtime.close()


async def test_authorization_refusal_precedes_any_sealing_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.application.services.resource_scope_service import ResourceScopeService

    runtime, command, identifier = await abandoned(tmp_path)
    store = SqliteOperationStore(runtime.ledger.engine)
    before = store.read(identifier)
    before_events = SqliteEventStore(runtime.ledger.engine).list_events(identifier)

    def denied(self: Any, operation: Any) -> None:
        del self
        assert operation.operation_id == identifier
        raise ResourceScopeError("RESOURCE_SCOPE_READ_DENIED")

    monkeypatch.setattr(ResourceScopeService, "require_operation", denied)
    try:
        response = await runtime.bus.dispatch(command)
        assert (
            response.error is not None
            and response.error.data["reason_code"] == "RESOURCE_SCOPE_READ_DENIED"
        )
        assert store.read(identifier) == before
        assert SqliteEventStore(runtime.ledger.engine).list_events(identifier) == before_events
        assert terminal_counts(runtime, identifier) == (0, 0)
    finally:
        runtime.close()
