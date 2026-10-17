"""Caller cancellation during common source cleanup must not publish or close twice."""

import asyncio
from pathlib import Path
from typing import cast

import httpx
import pytest
from sqlalchemy import func, select
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver, request, value
from tests.integration.test_a03_critical_counter_search import CriticalA03Model
from tests.integration.test_a03_multi_wave_counter_search import policy
from tests.integration.test_m2_counter_cancel_boundary import WaitingCleanupConnector

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.http.app import create_app
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.schema import artifacts, evidence_links, source_bindings
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.ingestion_service import IngestArtifactCommand, IngestionService
from thoth.domain.cleanup import CleanupUsage
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCheckpoint,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorFetchResult,
)
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import OperationState, ParserErrorCode
from thoth.domain.errors import ParserFailure
from thoth.domain.ingestion import IngestionResult
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcResponse


@pytest.mark.parametrize("failure", ("none", "fetch", "parse"))
@pytest.mark.parametrize("write_fault", (False, True))
async def test_source_connect_caller_cancel_during_cleanup_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, write_fault: bool
) -> None:
    connector = WaitingCleanupConnector()
    runtime = create_runtime(
        tmp_path,
        connector_registry=ConnectorRegistry((connector,)),
        model_resolver=StaticModelResolver(cast(ModelPort, CriticalA03Model())),
    )
    project = "project:source-cancel"
    original_create = ControlRecordService.create
    failed_writes: list[str] = []
    original_failure = (
        ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "fixture fetch failure")
        if failure == "fetch"
        else ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "fixture parse failure")
    )

    async def failed_fetch(
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        connector.fetch_count += 1
        raise original_failure

    async def failed_parse(
        service: IngestionService,
        command: IngestArtifactCommand,
        *,
        derived_parents: tuple[str, ...] | None = None,
    ) -> IngestionResult:
        raise original_failure

    if failure == "fetch":
        monkeypatch.setattr(connector, "fetch", failed_fetch)
    elif failure == "parse":
        monkeypatch.setattr(IngestionService, "stage_async", failed_parse)

    def create(
        records: ControlRecordService,
        *,
        project_id: str,
        namespace: str,
        record_type: str,
        state: str,
        payload: dict[str, object],
        record_id: str | None = None,
    ) -> ControlRecord:
        if write_fault and namespace == "CONNECTOR" and record_type == "CLEANUP_USAGE":
            failed_writes.append(state)
            raise RuntimeError("fixture cleanup observation storage failure")
        return original_create(
            records,
            project_id=project_id,
            namespace=namespace,
            record_type=record_type,
            state=state,
            payload=payload,
            record_id=record_id,
        )

    def counts() -> tuple[int, ...]:
        with runtime.ledger.engine.connect() as connection:
            return tuple(
                int(
                    connection.execute(
                        select(func.count()).select_from(table).where(table.c.project_id == project)
                    ).scalar_one()
                )
                for table in (artifacts, source_bindings, evidence_links)
            )

    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "Source cancellation fixture",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        selected = policy("converged", [])
        selected["connector_allowlist"] = ["mw-counter"]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {"project_id": project, "expected_revision": 0, "payload": selected},
                )
            )
        )
        before = counts()
        monkeypatch.setattr(ControlRecordService, "create", create)
        app = create_app(runtime.bus)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/rpc/async",
                json=request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": project,
                        "connector_id": "mw-counter",
                        "selector": {"relative_path": "counter.md"},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                ).model_dump(mode="json", by_alias=True),
            )
            assert response.status_code == 200
            identifier = str(response.json()["result"]["operation_id"])
            task = cast(asyncio.Task[JsonRpcResponse], app.state.background_tasks[identifier])
            await asyncio.wait_for(connector.cleanup_started.wait(), timeout=15)
            cancelled = await client.post(
                f"/operations/{identifier}/cancel", json={"project_id": project}
            )
            assert (
                cancelled.status_code == 200
                and cancelled.json()["result"]["value"]["cancelled"] is True
            )
            with pytest.raises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(task, timeout=10)
        assert task.cancelled()
        if failure != "none":
            assert raised.value.__context__ is original_failure
        operation = runtime.bus.read_operation(identifier)
        assert operation is not None and operation.state is OperationState.CANCELLED
        assert counts() == before == (0, 0, 0)
        assert connector.close_count == 1 and connector.cleanup_cancelled
        records = SqliteControlRecordStore(runtime.ledger.engine).list(
            project, "CONNECTOR", "CLEANUP_USAGE", latest_only=False
        )
        if write_fault:
            assert failed_writes == ["PENDING", "CANCEL_REQUESTED"] and records == ()
        else:
            assert len(records) == 2
            usages = [CleanupUsage.model_validate(record.payload) for record in records]
            assert [item.state for item in usages] == ["PENDING", "CANCEL_REQUESTED"]
            assert all(item.limit_ms == 2000 and item.remote_stop == "UNKNOWN" for item in usages)
    finally:
        connector.release_cleanup.set()
        await runtime.bus.drain()
        runtime.close()
