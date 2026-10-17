"""Async HTTP submission and operation routes; task state stays on the application."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

import orjson
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import ValidationError

from thoth.adapters.http.hosted_review import hosted_transport_authorized
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.ports.auth import HttpAuthenticationPort
from thoth.protocol.bus import CommandBus, DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse
from thoth.protocol.stdio import encode_response


class ClaimLocal(Protocol):
    def __call__(
        self, request: JsonRpcRequest, bus: CommandBus, *, local_actor_id: str
    ) -> JsonRpcResponse | DispatchTicket: ...


def mount_async_rpc_route(
    application: FastAPI,
    auth: HttpAuthenticationPort | None,
    local_actor_id: str,
    *,
    _resolve_bus_for_request: Callable[[FastAPI, Request], CommandBus],
    _claim_local_rpc: ClaimLocal,
    _authenticate_rpc: Callable[
        [HttpAuthenticationPort, Request, JsonRpcRequest],
        Awaitable[AuthenticatedActorContext | Response],
    ],
    _hosted_credential_rpc_block_request: Callable[[JsonRpcRequest], Response | None],
    _hosted_transport_denial: Callable[[str | int | None], Response | None],
) -> None:
    async def rpc_async_endpoint(request: Request) -> Response:
        try:
            rpc_request = JsonRpcRequest.model_validate(orjson.loads(await request.body()))
        except (orjson.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail="invalid JSON-RPC request") from exc
        blocked = _hosted_credential_rpc_block_request(rpc_request)
        if blocked is not None:
            return blocked
        if not hosted_transport_authorized(request):
            blocked = _hosted_transport_denial(rpc_request.id)
            assert blocked is not None
            return blocked
        active_bus = _resolve_bus_for_request(application, request)
        authenticated: AuthenticatedActorContext | None
        if auth is None:
            authenticated = None
            claimed = _claim_local_rpc(
                rpc_request,
                active_bus,
                local_actor_id=local_actor_id,
            )
        else:
            authentication = await _authenticate_rpc(auth, request, rpc_request)
            if isinstance(authentication, Response):
                return authentication
            authenticated = authentication
            with authenticated_actor_scope(authenticated):
                claimed = active_bus.claim(rpc_request)
        if isinstance(claimed, JsonRpcResponse):
            return Response(content=encode_response(claimed), media_type="application/json")
        assert isinstance(claimed, DispatchTicket)
        if authenticated is None:
            task = asyncio.create_task(active_bus.execute(claimed))
        else:
            with authenticated_actor_scope(authenticated):
                task = asyncio.create_task(active_bus.execute(claimed))
        tasks: dict[str, asyncio.Task[JsonRpcResponse]] = application.state.background_tasks
        tasks[claimed.operation.operation_id] = task

        def forget(_task: asyncio.Task[JsonRpcResponse]) -> None:
            tasks.pop(claimed.operation.operation_id, None)

        task.add_done_callback(forget)
        response = JsonRpcResponse(
            id=rpc_request.id,
            result={
                "operation_id": claimed.operation.operation_id,
                "state": "RUNNING",
                "value": None,
            },
        )
        return Response(content=encode_response(response), media_type="application/json")

    application.add_api_route("/rpc/async", rpc_async_endpoint, methods=["POST"])


def mount_operation_routes(
    application: FastAPI,
    auth: HttpAuthenticationPort | None,
    *,
    _hosted_transport_block: Callable[[Request], Response | None],
    _resolve_bus_for_request: Callable[[FastAPI, Request], CommandBus],
    _cancel_async_operation: Callable[
        [FastAPI, HttpAuthenticationPort | None, str, Request], Awaitable[Response]
    ],
) -> None:
    async def read_async_operation(
        operation_id: str, project_id: str, bus: CommandBus
    ) -> dict[str, object]:
        # This route is installed below with a request-aware wrapper in hosted mode.
        if auth is not None:
            raise HTTPException(
                status_code=403,
                detail="authenticated operation read requires RPC operation/read",
            )
        operation = bus.read_operation(operation_id)
        if operation is None or operation.project_id != project_id:
            raise HTTPException(status_code=404, detail="operation was not found")
        return operation.model_dump(mode="json")

    async def read_async_operation_endpoint(
        operation_id: str, project_id: str, request: Request
    ) -> Response:
        blocked = _hosted_transport_block(request)
        if blocked is not None:
            return blocked
        active_bus = _resolve_bus_for_request(application, request)
        payload = await read_async_operation(operation_id, project_id, active_bus)
        return Response(content=orjson.dumps(payload), media_type="application/json")

    application.add_api_route(
        "/operations/{operation_id}",
        read_async_operation_endpoint,
        methods=["GET"],
    )

    async def cancel_async_operation(operation_id: str, request: Request) -> Response:
        blocked = _hosted_transport_block(request)
        if blocked is not None:
            return blocked
        return await _cancel_async_operation(application, auth, operation_id, request)

    application.add_api_route(
        "/operations/{operation_id}/cancel",
        cancel_async_operation,
        methods=["POST"],
    )


async def cancel_async_operation(
    application: FastAPI,
    auth: HttpAuthenticationPort | None,
    operation_id: str,
    request: Request,
    *,
    _resolve_bus_for_request: Callable[[FastAPI, Request], CommandBus],
    _auth_denied: Callable[[str | int | None, str], Response],
) -> Response:
    body = cast(object, await request.json())
    mapping = cast(dict[object, object], body) if isinstance(body, dict) else {}
    project_id = mapping.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    authenticated: AuthenticatedActorContext | None = None
    if auth is not None:
        try:
            authenticated = await auth.authenticate_http(
                authorization=request.headers.get("authorization"),
                project_id=project_id,
                method="operation/cancel",
                requested_scope={},
            )
        except PermissionError as exc:
            return _auth_denied(None, str(exc))
    cancel_request = JsonRpcRequest.model_validate(
        {
            "id": f"cancel:{operation_id}",
            "method": "operation/cancel",
            "params": {
                "_meta": {"idempotencyKey": f"cancel:{operation_id}"},
                "input": {"project_id": project_id, "operation_id": operation_id},
            },
        }
    )
    active_bus = _resolve_bus_for_request(application, request)
    operation = active_bus.read_operation(operation_id)
    tasks: dict[str, asyncio.Task[JsonRpcResponse]] = application.state.background_tasks
    if (
        authenticated is not None
        and operation is not None
        and (
            operation.owner_actor_id != authenticated.actor_id
            or operation.owner_session_id != authenticated.session_id
            or operation.owner_role_assignment_id != authenticated.role_assignment_id
            or operation.owner_data_scopes != tuple(sorted(authenticated.data_scopes))
        )
    ):
        return _auth_denied(None, "AUTH_OPERATION_OWNER_DENIED")
    if authenticated is None:
        response = await active_bus.dispatch(cancel_request)
    else:
        with authenticated_actor_scope(authenticated):
            response = await active_bus.dispatch(cancel_request)
    if response.error is None and operation is not None and operation.project_id == project_id:
        task = tasks.get(operation_id)
        if task is not None:
            task.cancel()
    return Response(content=encode_response(response), media_type="application/json")
