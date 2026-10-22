"""Real LOCAL setup with a committed result and a deliberately absent success tail."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_operation_journal_failure_boundaries import NoModels

from thoth.adapters.storage.events import SqliteEventStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.event import Checkpoint
from thoth.domain.operation import OperationRecord
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcRequest


@dataclass
class Gap:
    runtime: AppRuntime
    command: JsonRpcRequest
    operation: OperationRecord
    model: NoModels


class TailInterrupted(RuntimeError):
    pass


async def prepare_gap(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: DeploymentMode = DeploymentMode.LOCAL,
) -> Gap:
    model = NoModels()
    runtime = create_runtime(workspace, model_resolver=model, deployment_mode=mode)
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
        command = request(
            "project/metadata/update",
            "gap",
            {
                "project_id": "project:j",
                "expected_revision": 0,
                "name": "Saved once",
            },
        )
        ticket = runtime.bus.claim(command)
        assert isinstance(ticket, DispatchTicket)
        original = SqliteEventStore.insert_checkpoint

        def fail(store: SqliteEventStore, point: Checkpoint) -> None:
            if point.operation_id == ticket.operation.operation_id:
                raise TailInterrupted("checkpoint before insert")
            original(store, point)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteEventStore, "insert_checkpoint", fail)
            with pytest.raises(TailInterrupted):
                await runtime.bus.execute(ticket)
        stored = runtime.bus.read_operation(ticket.operation.operation_id)
        assert stored is not None and stored.result is not None
        return Gap(runtime, command, stored, model)
    except BaseException:
        runtime.close()
        raise
