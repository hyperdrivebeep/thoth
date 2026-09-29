"""Normal local RPC settings consume the independent xAI OAuth catalog."""

import json
import time
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.http.app import create_app
from thoth.adapters.models import codex_oauth
from thoth.adapters.models.local_credentials import LocalModelCredentials
from thoth.adapters.models.xai_broker import broker_for_workspace
from thoth.adapters.models.xai_catalog import ThothXaiOAuthCatalog
from thoth.adapters.models.xai_model import XaiWorkspaceModel
from thoth.adapters.models.xai_profile import XaiCredential
from thoth.adapters.models.xai_responses import XAI_CONTROL
from thoth.adapters.storage.schema import checkpoints, events, operations, receipts
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability
from thoth.ports.model import ModelPort
from thoth.ports.model_credentials import ModelCredentialError

pytestmark = pytest.mark.usefixtures("xai_http_guard")
TModel = TypeVar("TModel", bound=BaseModel)


def isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "synthetic-home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "CODEX_HOME"):
        monkeypatch.setenv(name, str(home / name.lower()))

    def fake_home(_cls: type[Path]) -> Path:
        return home

    monkeypatch.setattr(Path, "home", classmethod(fake_home))

    def disconnected(_workspace: Path) -> dict[str, object]:
        return {"provider": "codex-oauth", "connected": False, "execution_eligible": False}

    monkeypatch.setattr(codex_oauth, "codex_oauth_status", disconnected)


async def create_project(runtime: AppRuntime, project_id: str) -> None:
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"create-{project_id}",
                {"project_id": project_id, "name": project_id, "cutoff_at": "2026-09-24T00:00:00Z"},
            )
        )
    )


class FakeResponsesXaiRoute(ModelPort):
    """The normal entry uses the real xAI serializer against a synthetic provider reply."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.oracle = ControlledResearchModel()
        self.sent: list[dict[str, object]] = []

    @property
    def control_capability(self) -> ModelControlCapability:
        return XAI_CONTROL

    def resolve(self, *, provider: str, model: str | None = None) -> ModelPort:
        assert provider == "xai-oauth" and model == "grok-4.6"
        return self

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        oracle = await self.oracle.structured(request)
        output = oracle.output.model_dump_json()

        def reply(wire: httpx.Request) -> httpx.Response:
            assert str(wire.url) == "https://api.x.ai/v1/responses"
            assert wire.headers["Authorization"] == "Bearer synthetic-access"
            body: dict[str, object] = json.loads(wire.content)
            self.sent.append(body)
            event = {
                "type": "response.completed",
                "response": {
                    "id": f"synthetic-{len(self.sent)}",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": output}]}
                    ],
                    "usage": {"input_tokens": 4, "output_tokens": 4},
                },
            }
            return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

        return await XaiWorkspaceModel(
            broker_for_workspace(self.workspace), transport=httpx.MockTransport(reply)
        ).structured(request)


@pytest.mark.asyncio
async def test_xai_oauth_is_explicit_and_survives_runtime_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_home(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    runtime = create_runtime(workspace)
    try:
        await create_project(runtime, "project:xai")
        login_state = value(
            await runtime.bus.query(
                request(
                    "model/credential/login/status",
                    "xai-login-before",
                    {"provider": "xai", "login_id": None},
                )
            )
        )
        assert login_state["state"] == "LOGIN_REQUIRED"
        missing_cancel = await runtime.bus.dispatch(
            request(
                "model/credential/login/cancel",
                "xai-cancel-missing",
                {"provider": "xai", "login_id": "not-a-login"},
            )
        )
        assert missing_cancel.error is not None
        before = value(
            await runtime.bus.query(
                request(
                    "model/settings/read",
                    "xai-before",
                    {"project_id": "project:xai"},
                )
            )
        )
        assert before["availability"] == "UNAVAILABLE"
        broker = broker_for_workspace(workspace)
        with broker.profile.lock():
            broker.profile.set_generation("synthetic-generation")
            broker.profile.save(
                XaiCredential(
                    "synthetic-access",
                    "synthetic-refresh",
                    time.time() + 3600,
                    "synthetic-generation",
                )
            )
        listed = value(
            await runtime.bus.query(
                request(
                    "model/credential/list",
                    "xai-account",
                    {"project_id": "system:workspace"},
                )
            )
        )
        row = next(row for row in listed["accounts"] if row["provider"] == "xai")
        assert row["available_model_providers"] == ["xai-oauth"]
        assert row["execution_eligible"] is True
        assert row["execution_verified"] is False
        assert "synthetic-access" not in str(listed)
        logged_in = value(
            await runtime.bus.query(
                request(
                    "model/credential/login/status",
                    "xai-login-after",
                    {"provider": "xai"},
                )
            )
        )
        assert logged_in["state"] == "CONNECTED"
        still_default = value(
            await runtime.bus.query(
                request(
                    "model/settings/read",
                    "xai-default",
                    {"project_id": "project:xai"},
                )
            )
        )
        assert still_default["availability"] == "UNAVAILABLE"
        assert any(item["provider"] == "xai-oauth" for item in still_default["model_options"])
        selected = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "xai-select",
                    {
                        "project_id": "project:xai",
                        "expected_digest": None,
                        "selection": {
                            "provider": "xai-oauth",
                            "model": "grok-4.6",
                            "reasoning_effort": "high",
                        },
                    },
                )
            )
        )
        assert selected["effective_settings"]["provider"] == "xai-oauth"
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        readback = value(
            await reopened.bus.query(
                request(
                    "model/settings/read",
                    "xai-reopen",
                    {"project_id": "project:xai"},
                )
            )
        )
        assert readback["availability"] == "AVAILABLE"
        assert readback["selection"] == selected["selection"]
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_normal_thread_uses_fake_xai_responses_and_persists_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_home(tmp_path, monkeypatch)
    workspace = tmp_path / "normal"
    broker = broker_for_workspace(workspace)
    with broker.profile.lock():
        broker.profile.set_generation("synthetic-normal")
        broker.profile.save(
            XaiCredential(
                "synthetic-access",
                "synthetic-refresh",
                time.time() + 3600,
                "synthetic-normal",
            )
        )
    route = FakeResponsesXaiRoute(workspace)
    runtime = create_runtime(
        workspace,
        model_resolver=route,
        model_catalog=ThothXaiOAuthCatalog(workspace),
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        await create_project(runtime, "project:xai-normal")
        selected = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "normal-select",
                    {
                        "project_id": "project:xai-normal",
                        "expected_digest": None,
                        "selection": {
                            "provider": "xai-oauth",
                            "model": "grok-4.6",
                            "reasoning_effort": "high",
                        },
                    },
                )
            )
        )
        assert selected["availability"] == "AVAILABLE"
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "normal-start",
                    {
                        "project_id": "project:xai-normal",
                        "problem": "Why is latency uncertain?",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(admitted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        assert route.sent
        assert all(body["model"] == "grok-4.6" for body in route.sent)
        assert all(body["reasoning"] == {"effort": "high"} for body in route.sent)
        current = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "normal-current",
                    {
                        "project_id": "project:xai-normal",
                        "thread_id": admitted["thread_id"],
                    },
                )
            )
        )
        assert current["current_result"]["completion"] == "TERMINAL"
    finally:
        runtime.close()
    reopened = create_runtime(
        workspace,
        model_resolver=route,
        model_catalog=ThothXaiOAuthCatalog(workspace),
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        persisted = value(
            await reopened.bus.query(
                request(
                    "thread/read",
                    "normal-persisted",
                    {
                        "project_id": "project:xai-normal",
                        "thread_id": admitted["thread_id"],
                    },
                )
            )
        )
        assert persisted["current_result"]["result"] == current["current_result"]["result"]
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_device_login_http_rpc_persists_integer_expiry_and_replays_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_home(tmp_path, monkeypatch)
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    workspace = tmp_path / "device-rpc"
    starts: list[httpx.Request] = []

    def device(request: httpx.Request) -> httpx.Response:
        starts.append(request)
        assert str(request.url) == "https://auth.x.ai/oauth2/device/code"
        return httpx.Response(
            200,
            json={
                "device_code": "synthetic-private-device",
                "user_code": "TEST-1234",
                "verification_uri": "https://grok.com/activate",
                "expires_in": 30,
                "interval": 900,
            },
        )

    broker = broker_for_workspace(workspace)
    broker.transport = httpx.MockTransport(device)
    runtime = create_runtime(workspace)
    app = create_app(runtime.bus)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    start_request = {
        "jsonrpc": "2.0",
        "id": "start-xai",
        "method": "model/credential/register",
        "params": {
            "_meta": {"idempotencyKey": "start-xai"},
            "input": {"project_id": "system:workspace", "provider": "xai", "api_key": ""},
        },
    }

    def rpc(method: str, key: str, values: dict[str, object]) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": values},
        }

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/rpc", json=start_request)
            assert response.status_code == 200, response.text
            started = response.json()["result"]
            assert started["state"] == "SUCCEEDED"
            dto = started["value"]
            assert dto["provider"] == "xai" and dto["state"] == "PENDING"
            assert type(dto["expires_at"]) is int
            assert "synthetic-private-device" not in response.text
            operation = runtime.bus.read_operation(started["operation_id"])
            assert operation is not None and operation.state.value == "SUCCEEDED"
            assert operation.result is not None
            assert "user_code" not in operation.result
            assert "verification_uri" not in operation.result
            assert operation.result["login_id"] == dto["login_id"]
            persisted = await client.get(
                f"/operations/{started['operation_id']}",
                params={"project_id": "system:workspace"},
            )
            assert persisted.status_code == 200
            assert persisted.json()["result"] == operation.result

            replay = await client.post("/rpc", json=start_request)
            assert replay.status_code == 200
            assert replay.json()["result"]["operation_id"] == started["operation_id"]
            assert replay.json()["result"]["value"] == operation.result
            assert len(starts) == 1

            status = await client.post(
                "/rpc/query",
                json=rpc(
                    "model/credential/login/status",
                    "status-xai",
                    {"provider": "xai", "login_id": dto["login_id"]},
                ),
            )
            assert status.status_code == 200
            assert status.json()["result"]["value"]["expires_at"] == dto["expires_at"]

            cancelled = await client.post(
                "/rpc",
                json=rpc(
                    "model/credential/login/cancel",
                    "cancel-xai",
                    {"provider": "xai", "login_id": dto["login_id"]},
                ),
            )
            assert cancelled.status_code == 200
            cancel_result = cancelled.json()["result"]
            assert cancel_result["state"] == "SUCCEEDED"
            assert cancel_result["value"]["state"] == "CANCELLED"
            assert type(cancel_result["value"]["expires_at"]) is int
            cancel_op = runtime.bus.read_operation(cancel_result["operation_id"])
            assert cancel_op is not None and cancel_op.result is not None
            assert cancel_op.result["state"] == "CANCELLED"
            assert "user_code" not in cancel_op.result
            assert "verification_uri" not in cancel_op.result
            after_cancel = await client.post(
                "/rpc/query",
                json=rpc(
                    "model/credential/login/status",
                    "after-cancel-xai",
                    {"provider": "xai", "login_id": dto["login_id"]},
                ),
            )
            assert after_cancel.status_code == 200
            assert after_cancel.json()["result"]["value"]["state"] == "CANCELLED"
            assert len(starts) == 1
            with runtime.ledger.engine.connect() as connection:
                stored = [
                    *connection.execute(
                        select(operations.c.result_json).where(
                            operations.c.operation_id.in_(
                                (started["operation_id"], cancel_result["operation_id"])
                            )
                        )
                    ).scalars(),
                    *connection.execute(
                        select(events.c.payload_json).where(
                            events.c.operation_id.in_(
                                (started["operation_id"], cancel_result["operation_id"])
                            )
                        )
                    ).scalars(),
                    *connection.execute(
                        select(checkpoints.c.payload_json).where(
                            checkpoints.c.operation_id.in_(
                                (started["operation_id"], cancel_result["operation_id"])
                            )
                        )
                    ).scalars(),
                    *connection.execute(
                        select(receipts.c.payload_json).where(
                            receipts.c.project_id == "system:workspace"
                        )
                    ).scalars(),
                ]
            assert all(
                not any(
                    secret in raw
                    for secret in (
                        "TEST-1234",
                        "https://grok.com/activate",
                        "synthetic-private-device",
                    )
                )
                for raw in stored
            ), "login display secret reached operation/event/checkpoint/receipt storage"
    finally:
        broker.close()
        runtime.close()


@pytest.mark.asyncio
async def test_manual_complete_secret_is_ephemeral_in_normal_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_home(tmp_path, monkeypatch)
    secret = "synthetic-code-secret-123"

    def complete(
        self: LocalModelCredentials,
        provider: str,
        login_id: str,
        response: str,
        auth_method: str,
    ) -> dict[str, object]:
        assert self.root == tmp_path / "manual"
        assert (provider, login_id, response, auth_method) == (
            "anthropic",
            "synthetic-attempt",
            secret,
            "claude_pkce",
        )
        return {
            "provider": provider,
            "login_id": login_id,
            "login_state": "PENDING",
            "display": secret,
            "reason_code": "LOGIN_PENDING",
        }

    monkeypatch.setattr(LocalModelCredentials, "submit_login_response", complete)
    runtime = create_runtime(tmp_path / "manual")
    try:
        payload = request(
            "model/credential/login/complete",
            "manual-secret-once",
            {
                "project_id": "system:workspace",
                "provider": "anthropic",
                "auth_method": "claude_pkce",
                "login_id": "synthetic-attempt",
                "response": secret,
            },
        )
        first_response = await runtime.bus.dispatch(payload)
        first = value(first_response)
        assert "display" not in first
        assert secret not in json.dumps(first)
        assert first_response.result is not None
        operation = runtime.bus.read_operation(str(first_response.result["operation_id"]))
        assert operation is not None and operation.result is not None
        assert "display" not in operation.result
        replay = value(await runtime.bus.dispatch(payload))
        assert replay == operation.result

        def reject(
            self: LocalModelCredentials, provider: str, login_id: str,
            response: str, auth_method: str,
        ) -> dict[str, object]:
            del self, provider, login_id, auth_method
            raise ModelCredentialError(response)

        monkeypatch.setattr(LocalModelCredentials, "submit_login_response", reject)
        rejected = await runtime.bus.dispatch(request(
            "model/credential/login/complete", "manual-secret-error",
            {"project_id": "system:workspace", "provider": "anthropic",
             "auth_method": "claude_pkce", "login_id": "synthetic-attempt",
             "response": secret},
        ))
        assert rejected.error is not None
        assert rejected.error.message == "MODEL_LOGIN_COMPLETE_FAILED"
        assert secret not in rejected.model_dump_json()
        with runtime.ledger.engine.connect() as connection:
            stored = [
                *connection.execute(select(operations.c.result_json)).scalars(),
                *connection.execute(select(events.c.payload_json)).scalars(),
                *connection.execute(select(checkpoints.c.payload_json)).scalars(),
                *connection.execute(select(receipts.c.payload_json)).scalars(),
            ]
        assert all(raw is None or secret not in raw for raw in stored)
    finally:
        runtime.close()
