from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import cast

import orjson
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError

from thoth import __version__
from thoth.adapters.http.async_routes import (
    cancel_async_operation,
    mount_async_rpc_route,
    mount_operation_routes,
)
from thoth.adapters.http.file_staging import stage_file_upload
from thoth.adapters.http.hosted_review import (
    hosted_dispatch_gate_enabled,
    hosted_mode,
    hosted_request_session,
    hosted_transport_authorized,
    is_hosted_credential_method,
)
from thoth.adapters.http.internal_routes import mount_internal_routes
from thoth.adapters.storage.workspace_owner import WorkspaceInUse
from thoth.apps.hosted_review_dispatch import hosted_dispatch
from thoth.apps.hosted_review_snapshot import (
    mark_workspace_live,
)
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.ports.auth import HttpAuthenticationPort
from thoth.protocol.bus import CommandBus, DispatchTicket
from thoth.protocol.jsonrpc import (
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    RpcErrorCode,
)
from thoth.protocol.stdio import encode_response, handle_json_line

LOCAL_ACTOR_ID = "human:local-user"
_LOCAL_ACTOR_FIELDS = frozenset({"actor_id", "actor_or_agent_ref", "actor_ref"})
_READ_ONLY_METHOD_MARKERS = ("/read", "/list", "/status", "/audit", "/history")


def _hosted_transport_denial(request_id: str | int | None = None) -> Response | None:
    if not hosted_mode():
        return None
    return Response(
        content=encode_response(
            JsonRpcResponse(
                id=request_id,
                error=JsonRpcError(
                    code=RpcErrorCode.AUTHORIZATION_DENIED,
                    message="hosted review session is required",
                    data={"reason_code": "HOSTED_REVIEW_SESSION_REQUIRED", "pre_io": True},
                ),
            )
        ),
        media_type="application/json",
        status_code=403,
    )


def _hosted_transport_block(request: Request, body: bytes | None = None) -> Response | None:
    if hosted_transport_authorized(request):
        return None
    request_id: str | int | None = None
    if body is not None:
        try:
            parsed: object = orjson.loads(body)
            if isinstance(parsed, dict):
                candidate_id = cast(dict[str, object], parsed).get("id")
                if isinstance(candidate_id, (str, int)):
                    request_id = candidate_id
        except orjson.JSONDecodeError:
            request_id = None
    return _hosted_transport_denial(request_id)


def _local_identity_denial(
    request: JsonRpcRequest,
    *,
    local_actor_id: str,
) -> JsonRpcResponse | None:
    if any(marker in request.method for marker in _READ_ONLY_METHOD_MARKERS):
        return None
    supplied = {
        field: request.params.input[field]
        for field in _LOCAL_ACTOR_FIELDS
        if field in request.params.input
    }
    mismatched = {field: value for field, value in supplied.items() if value != local_actor_id}
    if not mismatched:
        return None
    return JsonRpcResponse(
        id=request.id,
        error=JsonRpcError(
            code=RpcErrorCode.AUTHORIZATION_DENIED,
            message="local actor attribution is fixed",
            data={
                "reason_code": "LOCAL_ACTOR_OVERRIDE_DENIED",
                "expected_actor_id": local_actor_id,
                "pre_io": True,
            },
        ),
    )


async def _dispatch_local_rpc(
    body: bytes,
    bus: CommandBus,
    *,
    local_actor_id: str,
    query: bool = False,
) -> bytes:
    try:
        request = JsonRpcRequest.model_validate(orjson.loads(body))
    except (orjson.JSONDecodeError, ValidationError):
        return await handle_json_line(body, bus)
    denial = _local_identity_denial(request, local_actor_id=local_actor_id)
    dispatch = bus.query if query else bus.dispatch
    return encode_response(denial if denial is not None else await dispatch(request))


def _claim_local_rpc(
    request: JsonRpcRequest,
    bus: CommandBus,
    *,
    local_actor_id: str,
) -> JsonRpcResponse | DispatchTicket:
    denial = _local_identity_denial(request, local_actor_id=local_actor_id)
    return denial if denial is not None else bus.claim(request)


async def _dispatch_authenticated_rpc(
    body: bytes,
    bus: CommandBus,
    auth: HttpAuthenticationPort,
    http_request: Request,
    *,
    query: bool = False,
) -> Response:
    try:
        request = JsonRpcRequest.model_validate(orjson.loads(body))
    except (orjson.JSONDecodeError, ValidationError):
        return Response(
            content=encode_response(
                JsonRpcResponse(
                    id=None,
                    error=JsonRpcError(
                        code=RpcErrorCode.PARSE_ERROR,
                        message="invalid JSON",
                    ),
                )
            ),
            media_type="application/json",
            status_code=400,
        )
    authenticated = await _authenticate_rpc(auth, http_request, request)
    if isinstance(authenticated, Response):
        return authenticated
    with authenticated_actor_scope(authenticated):
        dispatch = bus.query if query else bus.dispatch
        payload = encode_response(await dispatch(request))
    return Response(content=payload, media_type="application/json")


def healthz() -> dict[str, str]:
    """Local liveness endpoint; no external dependency checks."""
    return {"status": "ok", "version": __version__}


def create_app(
    bus: CommandBus | None = None,
    *,
    auth: HttpAuthenticationPort | None = None,
    local_actor_id: str = LOCAL_ACTOR_ID,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        if hosted_mode() and hosted_dispatch_gate_enabled():
            hosted_dispatch.enable()
        try:
            yield
        finally:
            hosted_dispatch.disable()
            tasks: dict[str, asyncio.Task[JsonRpcResponse]] = application.state.background_tasks
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tuple(tasks.values()), return_exceptions=True)
            active_bus: CommandBus | None = application.state.bus
            if active_bus is not None:
                active_bus.close_tasks()
                await active_bus.drain()
            runtime: AppRuntime | None = application.state.runtime
            if runtime is not None:
                runtime.close()
            session_runtimes: dict[str, AppRuntime] = application.state.session_runtimes
            for session_runtime in session_runtimes.values():
                session_runtime.bus.close_tasks()
                await session_runtime.bus.drain()
                session_runtime.close()
            session_runtimes.clear()
            application.state.session_last_seen.clear()

    application = FastAPI(
        title="THOTH Local App Server",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.bus = bus
    application.state.runtime = None
    application.state.session_runtimes = {}
    application.state.session_last_seen = {}
    application.state.background_tasks = {}
    if bus is not None:
        _bind_operation_task_canceller(application, bus)
    application.add_api_route("/healthz", healthz, methods=["GET"])

    async def issue_auth_session(request: Request) -> Response:
        if hosted_mode() or auth is None:
            raise HTTPException(status_code=404, detail="local authentication is not configured")
        body = cast(object, await request.json())
        value = cast(dict[object, object], body) if isinstance(body, dict) else {}
        try:
            issued = await auth.issue_http_session(
                actor_id=str(value.get("actor_id") or ""),
                credential=request.headers.get("x-thoth-local-credential", ""),
                project_id=str(value.get("project_id") or ""),
                role_assignment_id=str(value.get("role_assignment_id") or ""),
            )
        except PermissionError as exc:
            return _auth_denied(None, str(exc))
        return Response(
            content=orjson.dumps(issued.model_dump(mode="json")),
            media_type="application/json",
        )

    application.add_api_route("/auth/session", issue_auth_session, methods=["POST"])

    async def rpc_endpoint(request: Request) -> Response:
        body = await request.body()
        blocked = _hosted_credential_rpc_block(body)
        if blocked is not None:
            return blocked
        blocked = _hosted_transport_block(request, body)
        if blocked is not None:
            return blocked
        active_bus = _resolve_bus_for_request(application, request)
        if auth is None:
            payload = await _dispatch_local_rpc(
                body,
                active_bus,
                local_actor_id=local_actor_id,
            )
            return Response(content=payload, media_type="application/json")
        return await _dispatch_authenticated_rpc(body, active_bus, auth, request)

    application.add_api_route("/rpc", rpc_endpoint, methods=["POST"])

    async def query_endpoint(request: Request) -> Response:
        body = await request.body()
        blocked = _hosted_credential_rpc_block(body)
        if blocked is not None:
            return blocked
        blocked = _hosted_transport_block(request, body)
        if blocked is not None:
            return blocked
        active_bus = _resolve_bus_for_request(application, request)
        if auth is None:
            payload = await _dispatch_local_rpc(
                body, active_bus, local_actor_id=local_actor_id, query=True
            )
            return Response(content=payload, media_type="application/json")
        return await _dispatch_authenticated_rpc(body, active_bus, auth, request, query=True)

    application.add_api_route("/rpc/query", query_endpoint, methods=["POST"])

    _mount_async_rpc_route(application, auth, local_actor_id)

    mount_operation_routes(
        application,
        auth,
        _hosted_transport_block=lambda request: _hosted_transport_block(request),
        _resolve_bus_for_request=lambda app, request: _resolve_bus_for_request(app, request),
        _cancel_async_operation=lambda app, auth, identifier, request: _cancel_async_operation(
            app, auth, identifier, request
        ),
    )

    async def stage_file(request: Request, file: UploadFile = File(...)) -> dict[str, object]:
        if not hosted_transport_authorized(request):
            raise HTTPException(status_code=403, detail="HOSTED_REVIEW_SESSION_REQUIRED")
        return await _stage_file(auth, request, file)

    application.add_api_route("/files/stage", stage_file, methods=["POST"])

    _mount_internal_routes(application)
    _mount_web_ui(application)
    return application



def _mount_async_rpc_route(
    application: FastAPI, auth: HttpAuthenticationPort | None, local_actor_id: str
) -> None:
    def claim_local(
        request: JsonRpcRequest, bus: CommandBus, *, local_actor_id: str
    ) -> JsonRpcResponse | DispatchTicket:
        return _claim_local_rpc(request, bus, local_actor_id=local_actor_id)

    mount_async_rpc_route(
        application,
        auth,
        local_actor_id,
        _resolve_bus_for_request=lambda app, request: _resolve_bus_for_request(app, request),
        _claim_local_rpc=claim_local,
        _authenticate_rpc=lambda auth, request, rpc: _authenticate_rpc(auth, request, rpc),
        _hosted_credential_rpc_block_request=lambda rpc: _hosted_credential_rpc_block_request(rpc),
        _hosted_transport_denial=lambda request_id: _hosted_transport_denial(request_id),
    )


def _mount_internal_routes(application: FastAPI) -> None:
    mount_internal_routes(
        application,
        _workspace_path=lambda: _workspace_path(),
        _resolve_bus_for_request=lambda app, request: _resolve_bus_for_request(app, request),
    )

def web_ui_dist() -> Path | None:
    raw = os.environ.get("THOTH_WEB_DIST")
    dist = (
        Path(raw)
        if raw
        else Path(__file__).resolve().parents[4] / "apps" / "web" / "dist"
    )
    if (dist / "index.html").is_file():
        return dist
    return None


def _mount_web_ui(application: FastAPI) -> None:
    dist = web_ui_dist()
    if dist is None:
        return
    from fastapi.staticfiles import StaticFiles

    application.mount("/", StaticFiles(directory=str(dist), html=True), name="web-ui")


async def _cancel_async_operation(
    application: FastAPI,
    auth: HttpAuthenticationPort | None,
    operation_id: str,
    request: Request,
) -> Response:
    return await cancel_async_operation(
        application,
        auth,
        operation_id,
        request,
        _resolve_bus_for_request=lambda app, request: _resolve_bus_for_request(app, request),
        _auth_denied=lambda request_id, reason: _auth_denied(request_id, reason),
    )


async def _stage_file(
    auth: HttpAuthenticationPort | None,
    request: Request,
    file: UploadFile,
) -> dict[str, object]:
    return await stage_file_upload(
        auth,
        request,
        file,
        _workspace_path_for_request=lambda request: _workspace_path_for_request(request),
    )


def _resolve_bus_for_request(application: FastAPI, request: Request) -> CommandBus:
    if not hosted_mode() or os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip():
        return _resolve_bus(application)
    if not hosted_transport_authorized(request):
        raise HTTPException(status_code=403, detail="HOSTED_REVIEW_SESSION_REQUIRED")
    session_id = hosted_request_session(request)
    if not session_id:
        raise HTTPException(status_code=403, detail="HOSTED_REVIEW_SESSION_REQUIRED")
    session_runtimes: dict[str, AppRuntime] = application.state.session_runtimes
    last_seen: dict[str, float] = application.state.session_last_seen
    now = time.monotonic()
    ttl = int(os.environ.get("HOSTED_REVIEW_SESSION_TTL_SECONDS", "14400"))
    max_sessions = int(os.environ.get("HOSTED_REVIEW_MAX_CONCURRENT_SESSIONS", "8"))
    if not application.state.background_tasks:
        for expired_id, seen_at in tuple(last_seen.items()):
            if now - seen_at <= ttl:
                continue
            expired = session_runtimes.get(expired_id)
            if expired is not None and expired.bus.has_running_research_tasks():
                continue
            session_runtimes.pop(expired_id, None)
            last_seen.pop(expired_id, None)
            if expired is not None:
                expired.close()
    runtime = session_runtimes.get(session_id)
    if runtime is not None:
        last_seen[session_id] = now
        return runtime.bus
    if len(session_runtimes) >= max_sessions:
        raise HTTPException(status_code=429, detail="HOSTED_REVIEW_SESSION_LIMIT")
    workspace = _workspace_path_for_request(request)
    runtime = create_runtime(workspace)
    session_runtimes[session_id] = runtime
    last_seen[session_id] = time.monotonic()
    if hosted_mode():
        mark_workspace_live(workspace)
    _bind_operation_task_canceller(application, runtime.bus)
    return runtime.bus


def _resolve_bus(application: FastAPI) -> CommandBus:
    existing: CommandBus | None = application.state.bus
    if existing is not None:
        return existing
    workspace = Path(os.environ.get("THOTH_WORKSPACE", ".thoth"))
    try:
        runtime = create_runtime(workspace)
    except WorkspaceInUse as error:
        # Only reached when the server was not started through the CLI (which owns the workspace
        # first). The path and process id are shown to the caller, never stored.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "WORKSPACE_IN_USE",
                "message_ko": error.message_ko,
                "message_en": error.message_en,
            },
        ) from error
    application.state.runtime = runtime
    application.state.bus = runtime.bus
    if hosted_mode():
        mark_workspace_live(workspace)
    _bind_operation_task_canceller(application, runtime.bus)
    return runtime.bus


def _bind_operation_task_canceller(application: FastAPI, bus: CommandBus) -> None:
    def cancel(operation_id: str) -> None:
        tasks: dict[str, asyncio.Task[JsonRpcResponse]] = application.state.background_tasks
        task = tasks.get(operation_id)
        if task is not None:
            task.cancel()

    bus.bind_operation_task_canceller(cancel)


def _workspace_path() -> Path:
    return Path(os.environ.get("THOTH_WORKSPACE", ".thoth")).resolve()


def _workspace_path_for_request(request: Request) -> Path:
    base_workspace = _workspace_path()
    if not hosted_mode() or os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip():
        return base_workspace
    session_id = hosted_request_session(request)
    if not session_id:
        return base_workspace
    digest = sha256(session_id.encode("utf-8")).hexdigest()[:12]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._-")[:80]
    return base_workspace / "hosted-review-sessions" / f"{safe or 'session'}-{digest}"


app = create_app()


async def _authenticate_rpc(
    auth: HttpAuthenticationPort,
    request: Request,
    rpc_request: JsonRpcRequest,
) -> AuthenticatedActorContext | Response:
    project_id = rpc_request.params.input.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return _auth_denied(rpc_request.id, "AUTH_PROJECT_SCOPE_REQUIRED")
    raw_scope = rpc_request.params.input.get("scope")
    requested_scope = dict(rpc_request.params.meta.data_scope)
    if isinstance(raw_scope, dict):
        requested_scope.update({str(key): str(value) for key, value in raw_scope.items()})
    try:
        return await auth.authenticate_http(
            authorization=request.headers.get("authorization"),
            project_id=project_id,
            method=rpc_request.method,
            requested_scope=requested_scope,
        )
    except PermissionError as exc:
        return _auth_denied(rpc_request.id, str(exc))


def _hosted_credential_rpc_block(body: bytes) -> Response | None:
    if not hosted_mode():
        return None
    try:
        request = JsonRpcRequest.model_validate(orjson.loads(body))
    except (orjson.JSONDecodeError, ValidationError):
        return None
    return _hosted_credential_rpc_block_request(request)


def _hosted_credential_rpc_block_request(request: JsonRpcRequest) -> Response | None:
    if not hosted_mode() or not is_hosted_credential_method(request.method):
        return None
    return Response(
        content=encode_response(
            JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=RpcErrorCode.AUTHORIZATION_DENIED,
                    message="HOSTED_REVIEW_CREDENTIAL_RPC_DENIED",
                    data={"reason_code": "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED", "pre_io": True},
                ),
            )
        ),
        media_type="application/json",
        status_code=403,
    )


def _auth_denied(request_id: str | int | None, reason_code: str) -> Response:
    return Response(
        content=encode_response(
            JsonRpcResponse(
                id=request_id,
                error=JsonRpcError(
                    code=RpcErrorCode.AUTHORIZATION_DENIED,
                    message="authenticated project scope denied",
                    data={"reason_code": reason_code, "pre_io": True},
                ),
            )
        ),
        media_type="application/json",
        status_code=403,
    )
