"""Normal HTTP cancellation at a real connector cleanup await, using synthetic sources."""

import asyncio
import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from sqlalchemy import select
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver, request, value
from tests.integration.test_a03_critical_counter_search import A03Connector, CriticalA03Model
from tests.integration.test_a03_multi_wave_counter_search import policy, route

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.http.app import create_app
from thoth.adapters.storage.acquisition import SqliteAcquisitionUnitOfWork
from thoth.adapters.storage.acquisition_trace import SqliteEvidenceUnitOfWork
from thoth.adapters.storage.schema import evidence_links, source_bindings
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.domain.acquisition import AcquisitionCommit, EvidenceAtomicCommit
from thoth.domain.enums import OperationState
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcResponse


class WaitingCleanupConnector(A03Connector):
    def __init__(self) -> None:
        super().__init__(
            "mw-counter",
            {"counter.md": b"# Independent\n\nfalsifies dominant independent result\n"},
        )
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.cleanup_cancelled = False

    async def close(self, connector_run_id: str) -> None:
        self.close_count += 1
        self.cleanup_started.set()
        try:
            await self.release_cleanup.wait()
        except asyncio.CancelledError:
            self.cleanup_cancelled = True
            raise


async def test_http_cancel_during_counter_cleanup_cannot_publish_new_counter_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = A03Connector(
        "mw-primary",
        {"primary.md": b"# Primary\n\nCurrent evidence supports the dominant explanation.\n"},
    )
    counter = WaitingCleanupConnector()
    runtime = create_runtime(
        tmp_path,
        connector_registry=ConnectorRegistry((primary, counter)),
        model_resolver=StaticModelResolver(cast(ModelPort, CriticalA03Model())),
    )
    project, thread = "project:counter-cancel", "thread:counter-cancel"
    operation_id: str | None = None
    acquisition_publish_states: list[str] = []
    evidence_publish_states: list[str] = []
    original_acquisition = SqliteAcquisitionUnitOfWork.commit
    original_evidence = SqliteEvidenceUnitOfWork.commit

    def acquire(unit: SqliteAcquisitionUnitOfWork, pending: AcquisitionCommit) -> None:
        if pending.evidence_source.connector_ref == "mw-counter":
            assert operation_id is not None
            operation = runtime.bus.read_operation(operation_id)
            assert operation is not None
            acquisition_publish_states.append(operation.state.value)
        original_acquisition(unit, pending)

    def publish(unit: SqliteEvidenceUnitOfWork, pending: EvidenceAtomicCommit) -> None:
        if pending.claim_candidate.applicability == "INDEPENDENT_CRITICAL_REVIEW_CANDIDATE":
            assert operation_id is not None
            operation = runtime.bus.read_operation(operation_id)
            assert operation is not None
            evidence_publish_states.append(operation.state.value)
        original_evidence(unit, pending)

    monkeypatch.setattr(SqliteAcquisitionUnitOfWork, "commit", acquire)
    monkeypatch.setattr(SqliteEvidenceUnitOfWork, "commit", publish)

    def bindings() -> list[dict[str, str]]:
        with runtime.ledger.engine.connect() as connection:
            return [
                dict(
                    binding_id=str(row.binding_id),
                    artifact_id=str(row.artifact_id),
                    state=str(row.state),
                )
                for row in connection.execute(
                    select(source_bindings).where(source_bindings.c.project_id == project)
                )
            ]

    def links() -> list[str]:
        with runtime.ledger.engine.connect() as connection:
            return list(
                connection.execute(
                    select(evidence_links.c.evidence_id).where(
                        evidence_links.c.project_id == project
                    )
                ).scalars()
            )

    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "Counter cancellation fixture",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "payload": policy(
                            "converged",
                            [route("wave-counter", "mw-counter", "counter.md", priority=100)],
                        ),
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "primary",
                    {
                        "project_id": project,
                        "connector_id": "mw-primary",
                        "selector": {"relative_path": "primary.md"},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "problem": "Run bounded multi-wave independent counter research.",
                        "scope": {"workstream": "multi-wave-critical"},
                    },
                )
            )
        )
        app = create_app(runtime.bus)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client:
            started = await client.post(
                "/rpc/async",
                json=request(
                    "thread/input", "cancel-counter", {"project_id": project, "thread_id": thread}
                ).model_dump(mode="json", by_alias=True),
            )
            assert started.status_code == 200
            operation_id = str(started.json()["result"]["operation_id"])
            task = cast(asyncio.Task[JsonRpcResponse], app.state.background_tasks[operation_id])
            await asyncio.wait_for(counter.cleanup_started.wait(), timeout=20)
            before_bindings, before_links = bindings(), links()
            cancelled = await client.post(
                f"/operations/{operation_id}/cancel", json={"project_id": project}
            )
            assert (
                cancelled.status_code == 200
                and cancelled.json()["result"]["value"]["cancelled"] is True
            )
            completed = await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True), timeout=15
            )
        after_bindings, after_links = bindings(), links()
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None and operation.state is OperationState.CANCELLED
        stored_thread = SqliteThreadStore(runtime.ledger.engine).read(thread)
        assert stored_thread is not None
        observation: dict[str, object] = {
            "before_bindings": len(before_bindings),
            "after_bindings": len(after_bindings),
            "before_evidence_links": len(before_links),
            "after_evidence_links": len(after_links),
            "cleanup_cancelled": counter.cleanup_cancelled,
            "task_cancelled": task.cancelled(),
            "task_result_type": type(completed[0]).__name__,
            "operation_state": operation.state.value,
            "thread_execution_state": stored_thread.execution_state.value,
            "new_binding_states": [
                row["state"]
                for row in after_bindings
                if row["binding_id"] not in {prior["binding_id"] for prior in before_bindings}
            ],
            "acquisition_publish_operation_states": acquisition_publish_states,
            "evidence_publish_operation_states": evidence_publish_states,
        }
        print(json.dumps(observation, sort_keys=True))
        assert counter.cleanup_cancelled
        assert after_bindings == before_bindings, observation
        assert after_links == before_links, observation
        assert task.cancelled(), observation
    finally:
        counter.release_cleanup.set()
        await runtime.bus.drain()
        runtime.close()
