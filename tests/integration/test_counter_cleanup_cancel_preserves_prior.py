"""Cancelling the next wave preserves an earlier committed counter-evidence checkpoint."""

import asyncio
import json
from pathlib import Path
from typing import cast

import httpx
from sqlalchemy import select
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver, request, value
from tests.integration.test_a03_critical_counter_search import A03Connector, CriticalA03Model
from tests.integration.test_a03_multi_wave_counter_search import policy, route
from tests.integration.test_m2_counter_cancel_boundary import WaitingCleanupConnector

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.http.app import create_app
from thoth.adapters.storage.schema import evidence_links, source_bindings
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.domain.enums import OperationState, ThreadExecutionState
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcResponse


async def test_cancelled_second_wave_does_not_remove_prior_committed_source_or_link(
    tmp_path: Path,
) -> None:
    primary = A03Connector(
        "mw-primary",
        {"primary.md": b"# Primary\n\nCurrent evidence supports the dominant explanation.\n"},
    )
    first = A03Connector(
        "mw-first",
        {"first.md": b"# First independent\n\nfalsifies dominant first independent result\n"},
    )
    second = WaitingCleanupConnector()
    runtime = create_runtime(
        tmp_path,
        connector_registry=ConnectorRegistry((primary, first, second)),
        model_resolver=StaticModelResolver(cast(ModelPort, CriticalA03Model())),
    )
    project, thread = "project:prior-counter", "thread:prior-counter"

    def state() -> tuple[list[tuple[str, str]], list[str]]:
        with runtime.ledger.engine.connect() as connection:
            bindings = [
                (str(row.binding_id), str(row.state))
                for row in connection.execute(
                    select(source_bindings)
                    .where(source_bindings.c.project_id == project)
                    .order_by(source_bindings.c.binding_id)
                )
            ]
            links = [
                json.dumps(dict(row), sort_keys=True, default=str)
                for row in connection.execute(
                    select(evidence_links)
                    .where(evidence_links.c.project_id == project)
                    .order_by(evidence_links.c.evidence_id)
                ).mappings()
            ]
            return bindings, links

    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "Prior wave fixture",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        routes = [
            route("first", "mw-first", "first.md", priority=100),
            route("second", "mw-counter", "counter.md", priority=90),
        ]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "payload": policy("converged", routes),
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
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/rpc/async",
                json=request(
                    "thread/input", "input", {"project_id": project, "thread_id": thread}
                ).model_dump(mode="json", by_alias=True),
            )
            assert response.status_code == 200
            identifier = str(response.json()["result"]["operation_id"])
            task = cast(asyncio.Task[JsonRpcResponse], app.state.background_tasks[identifier])
            await asyncio.wait_for(second.cleanup_started.wait(), timeout=20)
            before = state()
            assert len(before[0]) == 2 and len(before[1]) == 1
            assert all(status == "ACTIVE" for _, status in before[0])
            cancelled = await client.post(
                f"/operations/{identifier}/cancel", json={"project_id": project}
            )
            assert (
                cancelled.status_code == 200
                and cancelled.json()["result"]["value"]["cancelled"] is True
            )
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=10)
        assert task.cancelled() and second.close_count == 1
        assert state() == before
        operation = runtime.bus.read_operation(identifier)
        stored_thread = SqliteThreadStore(runtime.ledger.engine).read(thread)
        assert operation is not None and operation.state is OperationState.CANCELLED
        assert (
            stored_thread is not None and stored_thread.execution_state is ThreadExecutionState.IDLE
        )
    finally:
        second.release_cleanup.set()
        await runtime.bus.drain()
        runtime.close()
