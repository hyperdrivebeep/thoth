"""Mount hosted internal operations with authorization before body and storage access."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import orjson
from fastapi import FastAPI, HTTPException, Request, Response

from thoth.adapters.http.hosted_review import hosted_internal_authorized, hosted_request_session
from thoth.apps.hosted_review_dispatch import SNAPSHOT_UNCOMMITTED, hosted_dispatch
from thoth.apps.hosted_review_snapshot import (
    export_workspace_archive,
    mark_workspace_live,
    restore_workspace_archive,
    snapshot_file_count,
    sqlite_has_running,
    sqlite_path,
    workspace_is_live,
)
from thoth.apps.runtime import AppRuntime
from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import JsonRpcResponse, RpcErrorCode


def mount_internal_routes(
    application: FastAPI,
    *,
    _workspace_path: Callable[[], Path],
    _resolve_bus_for_request: Callable[[FastAPI, Request], CommandBus],
) -> None:
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
        if not os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip() and not hosted_request_session(
            request
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
