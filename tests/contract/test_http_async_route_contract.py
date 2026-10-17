"""Freeze async HTTP task ownership, auth context and the internal restore interlock."""

import ast
import asyncio
import importlib
import inspect
import subprocess
import sys
import textwrap
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import JsonValue
from tests.contract.test_async_http import StaticActorAuth

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.bundle import SqliteStoreBundle, SqliteStoreFactory
from thoth.application.commands import OperationCommandHandlers
from thoth.domain.auth import AuthenticatedActorContext, current_authenticated_actor
from thoth.domain.enums import OperationState
from thoth.ports.auth import HttpAuthenticationPort
from thoth.protocol.bus import CommandBus, DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse
from thoth.protocol.registry import MethodRegistry

http_app = importlib.import_module("thoth.adapters.http.app")


def test_import_time_app_registers_async_routes_without_runtime() -> None:
    script = """
import importlib
from fastapi.routing import APIRoute
module = importlib.import_module('thoth.adapters.http.app')
app = module.app
routes = {(route.path, method) for route in app.routes
          if isinstance(route, APIRoute) for method in (route.methods or set())}
assert {('/rpc/async', 'POST'), ('/operations/{operation_id}', 'GET'),
        ('/operations/{operation_id}/cancel', 'POST')} <= routes
assert app.state.runtime is None and app.state.bus is None
assert app.state.background_tasks == {} and app.state.session_runtimes == {}
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_async_route_retains_transport_and_ticket_assertions() -> None:
    route = next(
        route
        for route in http_app.app.routes
        if isinstance(route, APIRoute) and route.path == "/rpc/async"
    )
    source = textwrap.dedent(inspect.getsource(route.endpoint))
    assertions = [
        ast.get_source_segment(source, node)
        for node in sorted(
            (node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Assert)),
            key=lambda node: node.lineno,
        )
    ]
    assert assertions == [
        "assert blocked is not None",
        "assert isinstance(claimed, DispatchTicket)",
    ]


def owner() -> AuthenticatedActorContext:
    return AuthenticatedActorContext(
        actor_id="human:alice",
        session_id="session:alice",
        project_id="project:async",
        role_assignment_id="role:alice",
        role="researcher",
        capabilities=("READ", "WRITE"),
        data_scopes=("PROJECT", "THREAD"),
    )


def request_body(**extra: JsonValue) -> dict[str, JsonValue]:
    return {
        "jsonrpc": "2.0",
        "id": "start",
        "method": "project/create",
        "params": {
            "_meta": {"idempotencyKey": "start"},
            "input": {"project_id": "project:async", **extra},
        },
    }


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("THOTH_WEB_DIST", str(tmp_path / "missing-dist"))
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    monkeypatch.delenv("THOTH_REVIEW_SESSION_SECRET", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@asynccontextmanager
async def running_app(
    tmp_path: Path,
    auth: HttpAuthenticationPort | None = None,
    *,
    local_actor_id: str = "human:local-user",
) -> AsyncGenerator[
    tuple[FastAPI, SqliteStoreBundle, asyncio.Event, list[AuthenticatedActorContext | None]]
]:
    bundle = SqliteStoreFactory().open(tmp_path)
    blocker = asyncio.Event()
    observed: list[AuthenticatedActorContext | None] = []

    async def slow(_value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        observed.append(current_authenticated_actor())
        await blocker.wait()
        return {"completed": True}

    clock = SystemClock()
    registry = MethodRegistry()
    registry.register("project/create", slow)
    handlers = OperationCommandHandlers(bundle.operations, clock=clock)
    registry.register("operation/cancel", handlers.cancel)
    bus = CommandBus(registry, bundle.operations, clock, UuidIdGenerator())
    app = cast(FastAPI, http_app.create_app(bus, auth=auth, local_actor_id=local_actor_id))
    try:
        yield app, bundle, blocker, observed
    finally:
        blocker.set()
        tasks = tuple(
            cast(dict[str, asyncio.Task[JsonRpcResponse]], app.state.background_tasks).values()
        )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        bundle.close()


@pytest.mark.parametrize("authenticated", (False, True))
async def test_task_registry_is_shared_with_restore_and_forget_keeps_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authenticated: bool
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "fixture-session")
    actor = owner()
    auth = StaticActorAuth({"owner": actor}) if authenticated else None
    async with running_app(tmp_path, auth) as (app, bundle, blocker, observed):
        tasks = app.state.background_tasks
        headers = {"x-thoth-review-session": "fixture-session", "authorization": "Bearer owner"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/rpc/async", json=request_body(), headers=headers)
            assert response.status_code == 200
            result = response.json()["result"]
            assert result["state"] == "RUNNING" and result["value"] is None
            identifier = result["operation_id"]
            assert app.state.background_tasks is tasks and identifier in tasks
            task = tasks[identifier]
            blocked = await client.post(
                "/internal/workspace-restore", content=b"invalid archive", headers=headers
            )
            assert blocked.status_code == 200
            assert blocked.json() == {"skipped": True, "reason": "live-runtime"}
            assert app.state.background_tasks is tasks and tasks[identifier] is task
            blocker.set()
            await task
            await asyncio.sleep(0)
            assert app.state.background_tasks is tasks and tasks == {}
            assert observed == [actor if authenticated else None]
            saved = bundle.operations.read(identifier)
            assert saved is not None and saved.state is OperationState.SUCCEEDED
            assert saved.owner_actor_id == (actor.actor_id if authenticated else None)


@pytest.mark.parametrize("field", ("actor_id", "session_id", "role_assignment_id", "data_scopes"))
async def test_cancel_denies_each_owner_mismatch_without_touching_original_task(
    tmp_path: Path, field: str
) -> None:
    actor = owner()
    replacement = ("PROJECT",) if field == "data_scopes" else "other"
    other = actor.model_copy(update={field: replacement})
    auth = StaticActorAuth({"owner": actor, "other": other})
    async with (
        running_app(tmp_path, auth) as (app, bundle, _blocker, _observed),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        started = await client.post(
            "/rpc/async", json=request_body(), headers={"authorization": "Bearer owner"}
        )
        identifier = started.json()["result"]["operation_id"]
        task = app.state.background_tasks[identifier]
        record = bundle.operations.read(identifier)
        denied = await client.post(
            f"/operations/{identifier}/cancel",
            json={"project_id": "project:async"},
            headers={"authorization": "Bearer other"},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["data"]["reason_code"] == "AUTH_OPERATION_OWNER_DENIED"
        assert denied.json()["error"]["data"]["pre_io"] is True
        assert bundle.operations.read(identifier) == record
        assert app.state.background_tasks[identifier] is task and not task.cancelled()


async def test_matching_owner_scope_order_can_cancel_and_get_requires_rpc(tmp_path: Path) -> None:
    actor = owner()
    reordered = actor.model_copy(update={"data_scopes": tuple(reversed(actor.data_scopes))})
    auth = StaticActorAuth({"owner": actor, "reordered": reordered})
    async with (
        running_app(tmp_path, auth) as (app, bundle, _blocker, _observed),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        started = await client.post(
            "/rpc/async", json=request_body(), headers={"authorization": "Bearer owner"}
        )
        identifier = started.json()["result"]["operation_id"]
        direct = await client.get(
            f"/operations/{identifier}", params={"project_id": "project:async"}
        )
        assert direct.status_code == 403
        assert direct.json() == {
            "detail": "authenticated operation read requires RPC operation/read"
        }
        missing_auth = await client.post(
            f"/operations/{identifier}/cancel", json={"project_id": "project:async"}
        )
        assert missing_auth.status_code == 403
        assert missing_auth.json()["error"]["data"]["reason_code"] == "AUTH_SESSION_REQUIRED"
        cancelled = await client.post(
            f"/operations/{identifier}/cancel",
            json={"project_id": "project:async"},
            headers={"authorization": "Bearer reordered"},
        )
        assert (
            cancelled.status_code == 200
            and cancelled.json()["result"]["value"]["cancelled"] is True
        )
        await asyncio.sleep(0)
        saved = bundle.operations.read(identifier)
        assert saved is not None and saved.state is OperationState.CANCELLED


async def test_local_missing_or_wrong_project_read_and_empty_cancel(tmp_path: Path) -> None:
    async with (
        running_app(tmp_path) as (app, _bundle, _blocker, _observed),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        started = await client.post("/rpc/async", json=request_body())
        identifier = started.json()["result"]["operation_id"]
        for op, project in (("missing", "project:async"), (identifier, "other")):
            response = await client.get(f"/operations/{op}", params={"project_id": project})
            assert response.status_code == 404 and response.json() == {
                "detail": "operation was not found"
            }
        response = await client.post(f"/operations/{identifier}/cancel", json=[])
        assert response.status_code == 400 and response.json() == {
            "detail": "project_id is required"
        }


@pytest.mark.parametrize("case", ("invalid-json", "hosted-credential", "hosted-transport"))
async def test_async_preclaim_rejections_never_resolve_runtime(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")

    def forbidden(*_args: object) -> CommandBus:
        pytest.fail("rejected request resolved a runtime")

    monkeypatch.setattr(http_app, "_resolve_bus_for_request", forbidden)
    app = cast(FastAPI, http_app.create_app())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = request_body()
        if case == "invalid-json":
            response = await client.post("/rpc/async", content=b"{")
            assert response.status_code == 400
            assert response.json() == {"detail": "invalid JSON-RPC request"}
        else:
            if case == "hosted-credential":
                payload["method"] = "model/credential/list"
            response = await client.post("/rpc/async", json=payload)
            assert response.status_code == 403
            assert response.json()["error"]["data"]["reason_code"] == (
                "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED"
                if case == "hosted-credential"
                else "HOSTED_REVIEW_SESSION_REQUIRED"
            )
        assert app.state.background_tasks == {} and app.state.runtime is None


async def test_local_identity_denial_never_claims_or_creates_a_task(tmp_path: Path) -> None:
    async with (
        running_app(tmp_path) as (app, _bundle, _blocker, observed),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post("/rpc/async", json=request_body(actor_id="human:spoofed"))
        assert response.status_code == 200
        assert response.json()["error"]["data"]["reason_code"] == "LOCAL_ACTOR_OVERRIDE_DENIED"
        assert app.state.background_tasks == {} and observed == []


async def test_lifespan_cancels_task_from_the_same_registry(tmp_path: Path) -> None:
    async with running_app(tmp_path) as (app, bundle, blocker, observed):
        tasks = app.state.background_tasks
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            started = await client.post("/rpc/async", json=request_body())
            identifier = started.json()["result"]["operation_id"]
            task = tasks[identifier]
            await asyncio.sleep(0)
            assert observed == [None] and not blocker.is_set()
            assert app.state.background_tasks is tasks and not task.done()
        assert task.cancelled() and app.state.background_tasks is tasks and tasks == {}
        saved = bundle.operations.read(identifier)
        assert saved is not None and saved.state is OperationState.CANCELLED


async def test_custom_local_actor_is_forwarded_at_request_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forwarded: list[str] = []
    original = http_app._claim_local_rpc

    def claim(
        request: JsonRpcRequest, bus: CommandBus, *, local_actor_id: str
    ) -> JsonRpcResponse | DispatchTicket:
        forwarded.append(local_actor_id)
        return original(request, bus, local_actor_id=local_actor_id)

    async with (
        running_app(tmp_path, local_actor_id="human:custom-local") as (app, _, blocker, observed),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        monkeypatch.setattr(http_app, "_claim_local_rpc", claim)
        response = await client.post("/rpc/async", json=request_body(actor_id="human:custom-local"))
        assert response.status_code == 200 and response.json()["result"]["state"] == "RUNNING"
        task = app.state.background_tasks[response.json()["result"]["operation_id"]]
        blocker.set()
        await task
        assert forwarded == ["human:custom-local"] and observed == [None]
