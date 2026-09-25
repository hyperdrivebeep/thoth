import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.unit.test_post_audit_contracts import ref

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.services.connector_cleanup import cleanup_summary, connector_cleanup
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchBoundary, ResearchWork, research_work
from thoth.ports.model import ModelExecutionHold
from thoth.protocol.deferred import current_operation


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout,write_fault", [(False, False), (True, False), (False, True)])
async def test_cleanup_is_bounded_and_does_not_replace_parent_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout: bool,
    write_fault: bool,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    store = SqliteControlRecordStore(runtime.ledger.engine)
    records = ControlRecordService(store=store, clock=SystemClock(), ids=UuidIdGenerator())
    request = ConnectorAccessRequest(
        project_id="p",
        connector_id="fixture",
        actor_id="human:local-user",
        selector={"path": "data"},
        policy_id="p",
        policy_revision=1,
        policy_digest="a" * 64,
    )
    token = current_operation.set(
        OperationRecord(
            operation_id="operation:test",
            project_id="p",
            method="thread/start",
            idempotency_key="test",
            scope_digest="a" * 64,
            state=OperationState.SUCCEEDED,
            created_at=SystemClock().now(),
        )
    )
    calls = 0
    boundary = MagicMock(spec=ResearchBoundary)
    boundary.reserve.side_effect = ModelExecutionHold("TOTAL_BUDGET_EXHAUSTED")
    work_token = research_work.set(ResearchWork(ref(), "cleanup", cast(ResearchBoundary, boundary)))

    async def close() -> None:
        nonlocal calls
        calls += 1
        if timeout:
            await asyncio.Event().wait()

    if write_fault:

        def fail(**_kwargs: Any) -> None:
            raise RuntimeError("observation write fault")

        monkeypatch.setattr(records, "create", fail)
    try:
        usage = await connector_cleanup(request, "run:one", close, records)
        assert calls == 1
        assert usage.parent_operation_id == "operation:test"
        assert usage.limit_ms == 2000 and usage.remote_stop == "UNKNOWN"
        assert usage.state == ("CANCEL_REQUESTED" if timeout else "COMPLETED")
        assert usage.observation_persisted is not write_fault
        boundary.reserve.assert_not_called()
        repeated = await connector_cleanup(request, "run:one", close, records)
        assert repeated == usage and calls == 1
        if not write_fault:
            repeated = await connector_cleanup(request, "run:one", close, records)
            assert repeated == usage and calls == 1
            summary = cleanup_summary(store, "p", "operation:test")
            assert summary["cleanup_calls"] == 1
            assert cleanup_summary(store, "p", "other")["cleanup_calls"] == 0
    finally:
        research_work.reset(work_token)
        current_operation.reset(token)
        runtime.close()
