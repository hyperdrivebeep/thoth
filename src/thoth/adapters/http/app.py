from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from hashlib import sha256
from os import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import orjson
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError

from thoth import __version__
from thoth.adapters.http.hosted_review import (
    hosted_dispatch_gate_enabled,
    hosted_internal_authorized,
    hosted_mode,
    hosted_request_session,
    hosted_transport_authorized,
    is_hosted_credential_method,
)
from thoth.apps.hosted_review_dispatch import SNAPSHOT_UNCOMMITTED, hosted_dispatch
from thoth.apps.hosted_review_quota import (
    HostedReviewLimits,
    inbox_nbytes,
    reject_if_upload_exceeds,
)
from thoth.apps.hosted_review_snapshot import (
    export_workspace_archive,
    mark_workspace_live,
    restore_workspace_archive,
    snapshot_file_count,
    sqlite_has_running,
    sqlite_path,
    workspace_is_live,
)
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.upload_scope import project_upload_prefix
from thoth.ports.auth import HttpAuthenticationPort
from thoth.protocol.bus import CommandBus, DispatchTicket
from thoth.protocol.jsonrpc import (
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    RpcApplicationError,
    RpcErrorCode,
)
from thoth.protocol.stdio import encode_response, handle_json_line

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MEDIA_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".hwpx": "application/vnd.hancom.hwpx+zip",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
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


def _mount_internal_routes(application: FastAPI) -> None:
    async def export_workspace(request: Request) -> Response:
        if not hosted_internal_authorized(request):
            raise HTTPException(status_code=404, detail="not found")
        workspace = _workspace_path()
        payload = export_workspace_archive(workspace)
        running = sqlite_has_running(sqlite_path(workspace))
        return Response(
            content=payload,
            media_type="application/gzip",
            headers={
                "x-thoth-research-running": "1" if running else "0",
                "x-thoth-file-count": str(snapshot_file_count(workspace)),
            },
        )

    async def restore_workspace(request: Request) -> Response:
        if not hosted_internal_authorized(request):
            raise HTTPException(status_code=404, detail="not found")
        workspace = _workspace_path()
        tasks: dict[str, asyncio.Task[JsonRpcResponse]] = application.state.background_tasks
        if tasks or workspace_is_live(workspace):
            return Response(
                content=orjson.dumps({"skipped": True, "reason": "live-runtime"}),
                media_type="application/json",
            )
        runtime: AppRuntime | None = application.state.runtime
        if runtime is not None:
            runtime.close()
            application.state.runtime = None
            application.state.bus = None
        try:
            restored = restore_workspace_archive(workspace, await request.body())
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        mark_workspace_live(workspace)
        return Response(content=orjson.dumps(restored), media_type="application/json")

    async def release_dispatch(request: Request) -> Response:
        if not hosted_internal_authorized(request):
            raise HTTPException(status_code=404, detail="not found")
        try:
            payload: object = orjson.loads(await request.body())
        except orjson.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON") from exc
        operation_id = (
            cast(dict[str, object], payload).get("operation_id")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise HTTPException(status_code=400, detail="operation_id is required")
        hosted_dispatch.release(operation_id)
        return Response(
            content=orjson.dumps({"released": operation_id.strip()}),
            media_type="application/json",
        )

    async def abort_dispatch(request: Request) -> Response:
        if not hosted_internal_authorized(request):
            raise HTTPException(status_code=404, detail="not found")
        if (
            not os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip()
            and not hosted_request_session(request)
        ):
            raise HTTPException(status_code=400, detail="x-thoth-review-session is required")
        try:
            payload: object = orjson.loads(await request.body())
        except orjson.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON") from exc
        operation_id = (
            cast(dict[str, object], payload).get("operation_id")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise HTTPException(status_code=400, detail="operation_id is required")
        identifier = operation_id.strip()
        hosted_dispatch.abort(identifier)
        bus = _resolve_bus_for_request(application, request)
        failed = bus.fail_if_running(
            identifier,
            {
                "code": int(RpcErrorCode.DOMAIN_REJECTED),
                "message": SNAPSHOT_UNCOMMITTED,
                "data": {
                    "reason_code": SNAPSHOT_UNCOMMITTED,
                    "remote_observation": "NOT_SENT",
                    "pre_io": True,
                },
            },
        )
        return Response(
            content=orjson.dumps(
                {
                    "aborted": identifier,
                    "state": None if failed is None else failed.state.value,
                }
            ),
            media_type="application/json",
        )

    application.add_api_route("/internal/workspace-export", export_workspace, methods=["GET"])
    application.add_api_route("/internal/workspace-restore", restore_workspace, methods=["POST"])
    application.add_api_route("/internal/dispatch-release", release_dispatch, methods=["POST"])
    application.add_api_route("/internal/dispatch-abort", abort_dispatch, methods=["POST"])

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


async def _stage_file(
    auth: HttpAuthenticationPort | None,
    request: Request,
    file: UploadFile,
) -> dict[str, object]:
    workspace = _workspace_path_for_request(request)
    authenticated_project_id = request.headers.get("x-thoth-project-id", "")
    if not authenticated_project_id:
        raise HTTPException(status_code=400, detail="x-thoth-project-id is required")
    if auth is not None:
        try:
            authenticated = await auth.authenticate_http(
                authorization=request.headers.get("authorization"),
                project_id=authenticated_project_id,
                method="project/source/connect",
                requested_scope={},
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        authenticated_project_id = authenticated.project_id
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    media_type = MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise HTTPException(status_code=415, detail="unsupported research file type")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="empty research file")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="research file exceeds 64 MiB")
    if hosted_mode():
        try:
            reject_if_upload_exceeds(
                existing_bytes=inbox_nbytes(workspace),
                incoming_bytes=len(payload),
                limits=HostedReviewLimits(),
            )
        except RpcApplicationError as exc:
            raise HTTPException(status_code=413, detail=exc.message) from exc
    safe_name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", filename).strip(".-")
    if not safe_name:
        safe_name = f"source{suffix}"
    safe_stem = Path(safe_name).stem[:48] or "source"
    safe_name = f"{safe_stem}{suffix}"
    digest = sha256(payload).hexdigest()
    relative_inbox = project_upload_prefix(authenticated_project_id)
    inbox = workspace / "inbox" / relative_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{digest[:16]}-{safe_name}"
    if destination.exists():
        if sha256(destination.read_bytes()).hexdigest() != digest:
            raise HTTPException(status_code=409, detail="staged digest collision")
    else:
        temporary = inbox / f".{digest[:16]}-{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(payload)
            if sha256(temporary.read_bytes()).hexdigest() != digest:
                raise HTTPException(status_code=500, detail="staged file verification failed")
            replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    relative = destination.relative_to(workspace / "inbox")
    return {
        "relative_path": relative.as_posix(),
        "filename": filename,
        "media_type": media_type,
        "byte_sha256": digest,
        "bytes": len(payload),
    }


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
    runtime = create_runtime(workspace)
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
