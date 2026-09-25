from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue
from sqlalchemy import func, select

from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteLedger, SqliteOperationStore, migrate_sqlite_database
from thoth.adapters.storage.schema import operations as operations_table
from thoth.application.commands import OperationCommandHandlers
from thoth.domain.auth import AuthenticatedActorContext, IssuedAuthSession
from thoth.domain.enums import OperationState
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry


class StaticActorAuth:
    def __init__(self, contexts: dict[str, AuthenticatedActorContext]) -> None:
        self._contexts = contexts

    async def authenticate_http(
        self,
        *,
        authorization: str | None,
        project_id: str,
        method: str,
        requested_scope: dict[str, str],
    ) -> AuthenticatedActorContext:
        del method, requested_scope
        token = "" if authorization is None else authorization.removeprefix("Bearer ")
        context = self._contexts.get(token)
        if context is None:
            raise PermissionError("AUTH_SESSION_REQUIRED")
        if context.project_id != project_id:
            raise PermissionError("AUTH_PROJECT_SCOPE_DENIED")
        return context

    async def issue_http_session(
        self,
        *,
        actor_id: str,
        credential: str,
        project_id: str,
        role_assignment_id: str,
    ) -> IssuedAuthSession:
        del actor_id, credential, project_id, role_assignment_id
        raise PermissionError("AUTH_SESSION_REQUIRED")


def _actor_context(actor: str) -> AuthenticatedActorContext:
    return AuthenticatedActorContext(
        actor_id=actor,
        session_id=f"session:{actor}",
        project_id="project:async",
        role_assignment_id=f"role:{actor}",
        role="researcher",
        capabilities=("READ", "WRITE"),
        data_scopes=("PROJECT",),
    )


def _request() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "async-test",
        "method": "project/create",
        "params": {
            "_meta": {"idempotencyKey": "async-slow"},
            "input": {"project_id": "project:async"},
        },
    }


@pytest.mark.asyncio
async def test_async_rpc_returns_operation_then_cancels_running_task(
    tmp_path: Path,
) -> None:
    migrate_sqlite_database(tmp_path / "thoth.sqlite3")
    ledger = SqliteLedger(tmp_path / "thoth.sqlite3")
    ledger.initialize()
    operations = SqliteOperationStore(ledger.engine)
    clock = SystemClock()
    blocker = asyncio.Event()

    async def slow_handler(_value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        await blocker.wait()
        return {"completed": True}

    operation_handlers = OperationCommandHandlers(operations, clock=clock)
    registry = MethodRegistry()
    registry.register("project/create", slow_handler)
    registry.register("operation/cancel", operation_handlers.cancel)
    bus = CommandBus(registry, operations, clock, UuidIdGenerator())
    application = create_app(bus)
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post("/rpc/async", json=_request())
            assert started.status_code == 200
            payload = started.json()
            operation_id = payload["result"]["operation_id"]
            assert payload["result"]["state"] == "RUNNING"
            running = await client.get(
                f"/operations/{operation_id}", params={"project_id": "project:async"}
            )
            assert running.status_code == 200
            assert running.json()["state"] == "RUNNING"
            with ledger.engine.connect() as connection:
                operation_count = connection.execute(
                    select(func.count()).select_from(operations_table)
                ).scalar_one()
            assert operation_count == 1

            wrong_project = await client.post(
                f"/operations/{operation_id}/cancel",
                json={"project_id": "project:other"},
            )
            assert wrong_project.status_code == 200
            assert wrong_project.json()["error"]["code"] == -32022
            assert operation_id in application.state.background_tasks
            task = application.state.background_tasks[operation_id]
            assert task.cancelled() is False
            wrong_record = operations.read(operation_id)
            assert wrong_record is not None
            assert wrong_record.state == OperationState.RUNNING

            cancelled = await client.post(
                f"/operations/{operation_id}/cancel",
                json={"project_id": "project:async"},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["result"]["value"]["cancelled"] is True
            await asyncio.sleep(0)
            record = operations.read(operation_id)
            assert record is not None
            assert record.state == OperationState.CANCELLED
    finally:
        blocker.set()
        ledger.close()


@pytest.mark.asyncio
async def test_authenticated_async_cancel_requires_original_actor_session(
    tmp_path: Path,
) -> None:
    migrate_sqlite_database(tmp_path / "thoth.sqlite3")
    ledger = SqliteLedger(tmp_path / "thoth.sqlite3")
    ledger.initialize()
    operations = SqliteOperationStore(ledger.engine)
    clock = SystemClock()
    blocker = asyncio.Event()

    async def slow_handler(_value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        await blocker.wait()
        return {"completed": True}

    operation_handlers = OperationCommandHandlers(operations, clock=clock)
    registry = MethodRegistry()
    registry.register("project/create", slow_handler)
    registry.register("operation/cancel", operation_handlers.cancel)
    bus = CommandBus(registry, operations, clock, UuidIdGenerator())
    auth = StaticActorAuth(
        {"alice": _actor_context("human:alice"), "bob": _actor_context("human:bob")}
    )
    application = create_app(bus, auth=auth)
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/rpc/async",
                json=_request(),
                headers={"authorization": "Bearer alice"},
            )
            operation_id = started.json()["result"]["operation_id"]
            persisted = operations.read(operation_id)
            assert persisted is not None
            assert persisted.owner_actor_id == "human:alice"
            assert persisted.owner_session_id == "session:human:alice"
            with ledger.engine.connect() as connection:
                before_direct_denial = int(
                    connection.execute(
                        select(func.count()).select_from(operations_table)
                    ).scalar_one()
                )
            direct_denied = await client.post(
                "/rpc",
                json={
                    "id": "bob-direct-cancel",
                    "method": "operation/cancel",
                    "params": {
                        "_meta": {"idempotencyKey": "bob-direct-cancel"},
                        "input": {
                            "project_id": "project:async",
                            "operation_id": operation_id,
                        },
                    },
                },
                headers={"authorization": "Bearer bob"},
            )
            assert direct_denied.json()["error"]["data"]["reason_code"] == (
                "AUTH_OPERATION_OWNER_DENIED"
            )
            with ledger.engine.connect() as connection:
                after_direct_denial = int(
                    connection.execute(
                        select(func.count()).select_from(operations_table)
                    ).scalar_one()
                )
            assert after_direct_denial == before_direct_denial
            denied = await client.post(
                f"/operations/{operation_id}/cancel",
                json={"project_id": "project:async"},
                headers={"authorization": "Bearer bob"},
            )
            assert denied.status_code == 403
            assert denied.json()["error"]["data"]["reason_code"] == ("AUTH_OPERATION_OWNER_DENIED")
            assert application.state.background_tasks[operation_id].cancelled() is False
            target_task = application.state.background_tasks[operation_id]
            record = operations.read(operation_id)
            assert record is not None
            assert record.state == OperationState.RUNNING

            allowed = await client.post(
                "/rpc",
                json={
                    "id": "alice-direct-cancel",
                    "method": "operation/cancel",
                    "params": {
                        "_meta": {"idempotencyKey": "alice-direct-cancel"},
                        "input": {
                            "project_id": "project:async",
                            "operation_id": operation_id,
                        },
                    },
                },
                headers={"authorization": "Bearer alice"},
            )
            assert allowed.status_code == 200
            assert allowed.json()["result"]["value"]["cancelled"] is True
            await asyncio.sleep(0)
            assert target_task.cancelled() is True
            cancelled = operations.read(operation_id)
            assert cancelled is not None
            assert cancelled.state == OperationState.CANCELLED
    finally:
        blocker.set()
        ledger.close()
