"""V-F04b: preserve local HTTP behavior and reject hostile headers before writes.

The original 57 observations now pin the approved guard behavior. TestClient does
not prove browser delivery, DNS rebinding, preflight behavior or response access.
Only temporary workspaces and real local command handlers are used. Each MATRIX
line records committed row-count changes (not SQL statement counts).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thoth.adapters.http.app import create_app
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


@dataclass(frozen=True)
class RequestShape:
    name: str
    host: str = "127.0.0.1:8765"
    origin: str | None = None
    content_type: str = "application/json"

    def headers(self) -> dict[str, str]:
        result = {"host": self.host, "content-type": self.content_type}
        if self.origin is not None:
            result["origin"] = self.origin
        return result


SHAPES = (
    RequestShape("ui-ipv4", origin="http://127.0.0.1:8765"),
    RequestShape("ui-localhost", "localhost:8765", "http://localhost:8765"),
    RequestShape("ui-ipv6", "[::1]:8765", "http://[::1]:8765"),
    RequestShape("vite-ipv4", "127.0.0.1:5173", "http://127.0.0.1:5173"),
    RequestShape("vite-localhost", "localhost:5173", "http://localhost:5173"),
    RequestShape("local-no-origin"),
    RequestShape("plain-no-origin", content_type="text/plain"),
    RequestShape("foreign-origin-json", origin="http://evil.example"),
    RequestShape("foreign-origin-plain", origin="http://evil.example", content_type="text/plain"),
    RequestShape("foreign-host-only", "evil.example:8765"),
    RequestShape("rebind-host-origin", "evil.example:8765", "http://evil.example:8765"),
)


@pytest.fixture
def local_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[AppRuntime, TestClient, Path]]:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    monkeypatch.setenv("THOTH_WEB_DIST", str(tmp_path / "missing-dist"))
    # Keep optional provider discovery away from the developer's credentials.
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "synthetic-home" / name.lower()))
    runtime = create_runtime(workspace)
    try:
        with TestClient(create_app(runtime.bus), base_url="http://127.0.0.1:8765") as client:
            yield runtime, client, workspace
    finally:
        runtime.close()


def _request(method: str, key: str, project_id: str) -> dict[str, object]:
    value: dict[str, object] = {"project_id": project_id}
    if method == "project/create":
        value.update(name="Synthetic HTTP baseline", cutoff_at="2026-10-19T00:00:00Z")
    return {
        "jsonrpc": "2.0",
        "id": key,
        "method": method,
        "params": {"_meta": {"idempotencyKey": key}, "input": value},
    }


def _counts(runtime: AppRuntime) -> dict[str, int]:
    with runtime.ledger.engine.connect() as connection:
        tables = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).scalars()
        return {
            name: int(
                connection.exec_driver_sql(
                    'SELECT count(*) FROM "' + name.replace('"', '""') + '"'
                ).scalar_one()
            )
            for name in tables
        }


def _record(
    shape: RequestShape,
    endpoint: str,
    method: str,
    response: httpx.Response,
    before: dict[str, int],
    after: dict[str, int],
    **details: object,
) -> dict[str, int]:
    delta = {name: after[name] - count for name, count in before.items() if after[name] != count}
    print(
        "MATRIX "
        + json.dumps(
            {
                "shape": shape.name,
                "endpoint": endpoint,
                "method": method,
                "headers": shape.headers(),
                "http_status": response.status_code,
                "cors_headers": {
                    k: v for k, v in response.headers.items() if k.startswith("access-control-")
                },
                "row_deltas": delta,
                "net_new_rows": sum(delta.values()),
                **details,
            },
            sort_keys=True,
        )
    )
    return delta


@pytest.mark.parametrize("shape", SHAPES, ids=lambda shape: shape.name)
def test_local_http_header_baseline(
    local_api: tuple[AppRuntime, TestClient, Path], shape: RequestShape
) -> None:
    runtime, client, _workspace = local_api
    http = cast(httpx.Client, client)
    if shape.name in {
        "plain-no-origin",
        "foreign-origin-json",
        "foreign-origin-plain",
        "foreign-host-only",
        "rebind-host-origin",
    }:
        _denied_matrix(runtime, http, shape)
        return
    assert client.portal is not None
    for endpoint in ("/rpc", "/rpc/async"):
        project_id = "project:" + shape.name + ("-async" if endpoint.endswith("async") else "-sync")
        before = _counts(runtime)
        response = http.post(
            endpoint,
            content=json.dumps(_request("project/create", project_id, project_id)),
            headers=shape.headers(),
        )
        assert response.status_code == 200
        assert response.json().get("error") is None, response.text
        operation_id = response.json()["result"]["operation_id"]

        async def finish() -> None:
            application = cast(FastAPI, client.app)
            background_tasks = cast(
                dict[str, asyncio.Task[JsonRpcResponse]], application.state.background_tasks
            )
            tasks = tuple(background_tasks.values())
            await asyncio.gather(*tasks)

        client.portal.call(finish)
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None and operation.state.value == "SUCCEEDED"
        after = _counts(runtime)
        delta = _record(shape, endpoint, "project/create", response, before, after, executed=True)
        assert delta["projects"] == 1 and delta["operations"] == 1
        # Independent ordinary HTTP readback confirms the persisted domain effect.
        readback = http.post("/rpc/query", json=_request("project/read", "readback", project_id))
        assert readback.json()["result"]["value"]["project_id"] == project_id

    before = _counts(runtime)
    read = http.post(
        "/rpc/query",
        content=json.dumps(_request("project/read", "read-probe", project_id)),
        headers=shape.headers(),
    )
    assert read.status_code == 200
    assert read.json()["result"]["value"]["project_id"] == project_id
    assert not _record(
        shape, "/rpc/query", "project/read", read, before, _counts(runtime), executed=True
    )

    absent_id = "project:query-must-not-write"
    rejected = http.post(
        "/rpc/query",
        content=json.dumps(_request("project/create", "query-write", absent_id)),
        headers=shape.headers(),
    )
    assert rejected.status_code == 200
    assert rejected.json()["error"]["code"] == -32601
    assert rejected.json()["error"]["message"] == "method is not a read query"
    assert not _record(
        shape,
        "/rpc/query",
        "project/create",
        rejected,
        before,
        _counts(runtime),
        executed=False,
        rejection="method is not a read query",
    )

    # Real claim + real cancellation handler, without starting any model or worker.
    ticket = runtime.bus.claim(
        JsonRpcRequest.model_validate(_request("project/create", "cancel-target", project_id))
    )
    assert isinstance(ticket, DispatchTicket)
    target_id = ticket.operation.operation_id
    assert ticket.operation.state.value == "RUNNING"
    before = _counts(runtime)
    cancel = http.post(
        f"/operations/{target_id}/cancel",
        content=json.dumps({"project_id": project_id}),
        headers=shape.headers(),
    )
    assert cancel.status_code == 200
    assert cancel.json()["result"]["value"]["cancelled"] is True
    target = runtime.bus.read_operation(target_id)
    assert target is not None and target.state.value == "CANCELLED"
    delta = _record(
        shape,
        "/operations/<id>/cancel",
        "operation/cancel",
        cancel,
        before,
        _counts(runtime),
        executed=True,
        updated_target_rows=1,
        target_before="RUNNING",
        target_after="CANCELLED",
    )
    assert delta["operations"] == 1 and "projects" not in delta


def _assert_denied(response: httpx.Response, shape: RequestShape) -> None:
    expected = {
        "plain-no-origin": (415, "JSON_CONTENT_TYPE_REQUIRED"),
        "foreign-origin-json": (403, "LOCAL_ORIGIN_REJECTED"),
        "foreign-origin-plain": (403, "LOCAL_ORIGIN_REJECTED"),
        "foreign-host-only": (403, "LOCAL_HOST_REJECTED"),
        "rebind-host-origin": (403, "LOCAL_HOST_REJECTED"),
    }[shape.name]
    assert (response.status_code, response.json()["code"]) == expected
    assert response.json()["message_ko"] and response.json()["message_en"]
    assert "evil.example" not in response.text
    assert not any(key.startswith("access-control-") for key in response.headers)


def _denied_matrix(runtime: AppRuntime, http: httpx.Client, shape: RequestShape) -> None:
    project_id = "project:denied"
    for endpoint, method in (
        ("/rpc", "project/create"),
        ("/rpc/async", "project/create"),
        ("/rpc/query", "project/read"),
        ("/rpc/query", "project/create"),
    ):
        before = _counts(runtime)
        response = http.post(
            endpoint,
            content=json.dumps(_request(method, endpoint + method, project_id)),
            headers=shape.headers(),
        )
        _assert_denied(response, shape)
        assert not _record(
            shape,
            endpoint,
            method,
            response,
            before,
            _counts(runtime),
            executed=False,
            rejection=response.json()["code"],
        )
        assert _counts(runtime)["projects"] == 0
    ticket = runtime.bus.claim(
        JsonRpcRequest.model_validate(_request("project/create", "cancel-target", project_id))
    )
    assert isinstance(ticket, DispatchTicket)
    before = _counts(runtime)
    original = runtime.bus.read_operation(ticket.operation.operation_id)
    response = http.post(
        f"/operations/{ticket.operation.operation_id}/cancel",
        content=json.dumps({"project_id": project_id}),
        headers=shape.headers(),
    )
    _assert_denied(response, shape)
    assert runtime.bus.read_operation(ticket.operation.operation_id) == original
    assert not _record(
        shape,
        "/operations/<id>/cancel",
        "operation/cancel",
        response,
        before,
        _counts(runtime),
        executed=False,
        rejection=response.json()["code"],
        updated_target_rows=0,
        target_before="RUNNING",
        target_after="RUNNING",
    )


@pytest.mark.parametrize("with_project_header", (False, True))
def test_file_staging_foreign_origin_is_rejected(
    local_api: tuple[AppRuntime, TestClient, Path], with_project_header: bool
) -> None:
    runtime, client, workspace = local_api
    http = cast(httpx.Client, client)
    headers = {"origin": "http://evil.example"}
    if with_project_header:
        headers["x-thoth-project-id"] = "project:synthetic-upload"
    before = _counts(runtime)
    response = http.post(
        "/files/stage",
        headers=headers,
        files={"file": ("synthetic.txt", b"synthetic only", "text/plain")},
    )
    files = [p for p in (workspace / "inbox").rglob("*") if p.is_file()]
    assert response.status_code == 403
    assert response.json()["code"] == "LOCAL_ORIGIN_REJECTED"
    assert files == []
    assert not _record(
        RequestShape(
            "file-header" if with_project_header else "file-no-header",
            origin=headers["origin"],
            content_type="multipart/form-data",
        ),
        "/files/stage",
        "file-stage",
        response,
        before,
        _counts(runtime),
        executed=False,
        staged_files=len(files),
        custom_project_header=with_project_header,
    )
