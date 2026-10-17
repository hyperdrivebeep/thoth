"""The research discovery path also propagates caller cancellation during failure cleanup."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_multi_wave_counter_search import policy
from tests.integration.test_m2_counter_cancel_boundary import WaitingCleanupConnector
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.schema import artifacts, evidence_links, source_bindings
from thoth.application.services.connector_service import ConnectorService
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCheckpoint,
    ConnectorErrorCode,
    ConnectorFailure,
)
from thoth.domain.enums import OperationState, ThreadExecutionState


class DiscoveryFailure(WaitingCleanupConnector):
    def __init__(self) -> None:
        super().__init__()
        self.close_count: int = 0
        self.capability = self.capability.model_copy(update={"connector_id": "a02-readonly"})
        self.failure = ConnectorFailure(
            ConnectorErrorCode.DRIVER_ERROR, "fixture discovery failure"
        )

    async def discover(
        self, request: ConnectorAccessRequest, checkpoint: ConnectorCheckpoint | None = None
    ) -> tuple[ConnectorArtifactRef, ...]:
        self.discover_count += 1
        raise self.failure


async def test_normal_research_discovery_failure_cleanup_preserves_caller_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = DiscoveryFailure()
    model = ControlledResearchModel(missing=True, discover=True)
    runtime = await setup(
        tmp_path, model, source=False, connector_registry=ConnectorRegistry((connector,))
    )
    original = ConnectorService.discover_candidates
    cancellations: list[asyncio.CancelledError] = []

    async def discover(service: ConnectorService, command: ConnectorAccessRequest):
        try:
            return await original(service, command)
        except asyncio.CancelledError as error:
            cancellations.append(error)
            raise

    monkeypatch.setattr(ConnectorService, "discover_candidates", discover)
    try:
        selected = policy("converged", [])
        selected["connector_allowlist"] = ["a02-readonly"]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {"project_id": "p", "expected_revision": 0, "payload": selected},
                )
            )
        )
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "discovery-cancel",
                    {
                        "project_id": "p",
                        "problem": "Find the missing connected evidence",
                        "contract_version": 2,
                    },
                )
            )
        )
        identifier, thread_id = accepted["operation_id"], accepted["thread_id"]
        assert isinstance(identifier, str) and isinstance(thread_id, str)
        await asyncio.wait_for(connector.cleanup_started.wait(), timeout=15)
        cancelled = value(
            await runtime.bus.dispatch(
                request(
                    "operation/cancel", "cancel", {"project_id": "p", "operation_id": identifier}
                )
            )
        )
        assert cancelled["cancelled"] is True
        await asyncio.wait_for(runtime.bus.drain(), timeout=10)
        assert len(cancellations) == 1 and cancellations[0].__context__ is connector.failure
        assert (
            connector.discover_count == connector.close_count == 1 and connector.cleanup_cancelled
        )
        assert connector.fetch_count == 0
        operation = runtime.bus.read_operation(identifier)
        assert operation is not None and operation.state is OperationState.CANCELLED
        thread = research_host(runtime).threads.read(thread_id)
        assert thread is not None and thread.execution_state is ThreadExecutionState.IDLE
        with runtime.ledger.engine.connect() as connection:
            assert all(
                connection.execute(
                    select(func.count()).select_from(table).where(table.c.project_id == "p")
                ).scalar_one()
                == 0
                for table in (artifacts, source_bindings, evidence_links)
            )
    finally:
        connector.release_cleanup.set()
        await runtime.bus.drain()
        runtime.close()
