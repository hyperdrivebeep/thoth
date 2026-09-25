from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteAuthSessionStore, SqliteGovernanceStore
from thoth.adapters.storage.schema import operations
from thoth.application.services import LocalAuthenticationService
from thoth.apps.runtime import AppRuntime, create_runtime


def auth_service(runtime: AppRuntime) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        sessions=SqliteAuthSessionStore(runtime.ledger.engine),
        governance=SqliteGovernanceStore(runtime.ledger.engine),
        credentials=StaticCredentialVerifier(
            {
                "human:" + actor: hashlib.sha256(("test-" + actor).encode()).hexdigest()
                for actor in ("alpha", "peer", "beta", "admin")
            }
        ),
        tokens=SecureSessionTokenIssuer(),
        clock=SystemClock(),
        ids=UuidIdGenerator(),
    )


@dataclass
class ScopeHarness:
    runtime: AppRuntime
    client: httpx.AsyncClient
    project: str
    tokens: dict[str, str]
    scopes: dict[str, str]
    target: str
    broad_target: str
    legacy_target: str
    private_target: str = ""

    async def rpc(
        self,
        actor: str,
        method: str,
        key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        scope = self.scopes[actor]
        meta: dict[str, Any] = {"idempotencyKey": key}
        if scope != "PROJECT":
            meta["dataScope"] = {"workstream": scope.removeprefix("WORKSTREAM:")}
        response = await self.client.post(
            "/rpc",
            json={
                "id": key,
                "method": method,
                "params": {"_meta": meta, "input": payload},
            },
            headers={"authorization": "Bearer " + self.tokens[actor]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def count(self) -> int:
        with self.runtime.ledger.engine.connect() as connection:
            return int(
                connection.execute(select(func.count()).select_from(operations)).scalar_one()
            )


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[ScopeHarness]:
    runtime, project = await prepare_project(tmp_path / "scope")
    tokens: dict[str, str] = {}
    scopes = {
        "alpha": "WORKSTREAM:alpha",
        "peer": "WORKSTREAM:alpha",
        "beta": "WORKSTREAM:beta",
        "admin": "PROJECT",
    }
    roles: dict[str, str] = {}
    for revision, (actor, scope) in enumerate(scopes.items()):
        assigned = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    actor,
                    {
                        "project_id": project,
                        "expected_revision": revision,
                        "actor_id": "human:" + actor,
                        "role": "researcher",
                        "scope": scope,
                        "authority_tags": ["CAP_READ", "CAP_WRITE", "CAP_THREAD"],
                    },
                )
            )
        )
        roles[actor] = str(assigned["role"]["role_assignment_id"])
    legacy = await runtime.bus.dispatch(
        request("project/read", "local-read", {"project_id": project})
    )
    assert legacy.result is not None
    transport = httpx.ASGITransport(app=create_app(runtime.bus, auth=auth_service(runtime)))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for alias in (*scopes, "renewed"):
                actor = "alpha" if alias == "renewed" else alias
                issued = await client.post(
                    "/auth/session",
                    json={
                        "actor_id": "human:" + actor,
                        "project_id": project,
                        "role_assignment_id": roles[actor],
                    },
                    headers={"x-thoth-local-credential": "test-" + actor},
                )
                assert issued.status_code == 200
                tokens[alias] = str(issued.json()["bearer_token"])
            scopes["renewed"] = scopes["alpha"]
            result = ScopeHarness(
                runtime,
                client,
                project,
                tokens,
                scopes,
                "",
                "",
                str(legacy.result["operation_id"]),
            )
            for actor, key in (("alpha", "scoped-thread"), ("admin", "broad-thread")):
                created = await result.rpc(
                    actor,
                    "thread/start",
                    key,
                    {
                        "project_id": project,
                        "thread_id": "thread:" + key,
                        "problem": "Compare available measurements",
                        "scope": {"workstream": "alpha"},
                    },
                )
                assert "error" not in created
                operation_id = str(created["result"]["operation_id"])
                if actor == "alpha":
                    result.private_target = operation_id
                    # The Thread container is shared by the existing workstream
                    # contract; the creation result also contains private drafts.
                    readable = await result.rpc(
                        actor,
                        "thread/read",
                        "scoped-thread-read",
                        {
                            "project_id": project,
                            "thread_id": "thread:" + key,
                        },
                    )
                    assert "error" not in readable
                    result.target = str(readable["result"]["operation_id"])
                else:
                    result.broad_target = operation_id
            yield result
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "method",
    [
        "operation/read",
        "operation/result/read",
        "operation/checkpoint/read",
        "operation/pause",
        "operation/resume",
        "operation/cancel",
    ],
)
async def test_wrong_persisted_scope_denied_before_operation_claim(
    harness: ScopeHarness,
    method: str,
) -> None:
    payload: dict[str, Any] = {
        "project_id": harness.project,
        "operation_id": harness.target,
    }
    if method in {"operation/pause", "operation/resume"}:
        payload["expected_checkpoint_digest"] = "0" * 64
    target_before = harness.runtime.bus.read_operation(harness.target)
    before = harness.count()
    denied = await harness.rpc("beta", method, "denied:" + method, payload)
    assert denied["error"]["data"] == {
        "reason_code": "AUTH_OPERATION_SCOPE_DENIED",
        "pre_io": True,
    }
    assert harness.count() == before
    assert harness.runtime.bus.read_operation(harness.target) == target_before


@pytest.mark.parametrize("actor", ["alpha", "peer", "renewed", "admin"])
async def test_same_scope_and_project_readers_keep_result_checkpoint_access(
    harness: ScopeHarness,
    actor: str,
) -> None:
    for method in ("operation/read", "operation/result/read", "operation/checkpoint/read"):
        response = await harness.rpc(
            actor,
            method,
            actor + method,
            {
                "project_id": harness.project,
                "operation_id": harness.target,
            },
        )
        assert "error" not in response
        returned = response["result"]["value"]
        if method == "operation/checkpoint/read":
            assert returned["checkpoint"]["operation_id"] == harness.target
        else:
            assert returned["operation_id"] == harness.target


@pytest.mark.parametrize("actor", ["peer", "admin"])
async def test_container_read_permission_does_not_publish_private_creation_drafts(
    harness: ScopeHarness, actor: str
) -> None:
    response = await harness.rpc(
        actor,
        "operation/result/read",
        "private-draft-result",
        {
            "project_id": harness.project,
            "operation_id": harness.private_target,
        },
    )
    assert response["error"]["data"]["reason_code"] == "RESOURCE_LINEAGE_UNKNOWN"


@pytest.mark.parametrize("actor", ["alpha", "peer", "renewed", "admin"])
@pytest.mark.parametrize("method", ["operation/pause", "operation/resume"])
async def test_same_scope_checkpoint_controls_reach_full_handler_validation(
    harness: ScopeHarness,
    actor: str,
    method: str,
) -> None:
    payload: dict[str, Any] = {
        "project_id": harness.project,
        "operation_id": harness.target,
    }
    checkpoint = await harness.rpc(
        actor, "operation/checkpoint/read", actor + ":control-checkpoint", payload
    )
    assert "error" not in checkpoint
    digest = checkpoint["result"]["value"]["checkpoint"]["checkpoint_digest"]
    payload["expected_checkpoint_digest"] = digest
    allowed = await harness.rpc(actor, method, actor + ":control:" + method, payload)
    assert "error" not in allowed
    assert allowed["result"]["value"]["checkpoint_digest"] == digest
    assert allowed["result"]["value"]["reason"] == (
        "NO_METHOD_SPECIFIC_PAUSE_HANDLER"
        if method == "operation/pause"
        else "NO_METHOD_SPECIFIC_RESUME_HANDLER"
    )
    payload["expected_checkpoint_digest"] = "0" * 64
    stale = await harness.rpc(actor, method, actor + ":stale:" + method, payload)
    assert stale["error"]["code"] == -32031


@pytest.mark.parametrize("target_name", ["broad_target", "legacy_target"])
async def test_actor_permission_envelope_is_not_narrowed_to_result_workstream(
    harness: ScopeHarness,
    target_name: str,
) -> None:
    target = getattr(harness, target_name)
    for method in ("operation/read", "operation/result/read", "operation/checkpoint/read"):
        before = harness.count()
        payload = {"project_id": harness.project, "operation_id": target}
        denied = await harness.rpc("alpha", method, "limited:" + method, payload)
        assert denied["error"]["data"]["reason_code"] == "AUTH_OPERATION_SCOPE_DENIED"
        assert harness.count() == before
        permitted = await harness.rpc("admin", method, "admin:" + method, payload)
        assert "error" not in permitted


@pytest.mark.parametrize("actor", ["peer", "renewed", "admin"])
async def test_cancellation_still_requires_exact_origin_session(
    harness: ScopeHarness,
    actor: str,
) -> None:
    before = harness.count()
    denied = await harness.rpc(
        actor,
        "operation/cancel",
        "cancel:" + actor,
        {
            "project_id": harness.project,
            "operation_id": harness.target,
        },
    )
    assert denied["error"]["data"]["reason_code"] == "AUTH_OPERATION_OWNER_DENIED"
    assert harness.count() == before
    origin = await harness.rpc(
        "alpha",
        "operation/cancel",
        "cancel:origin",
        {
            "project_id": harness.project,
            "operation_id": harness.target,
        },
    )
    assert "error" not in origin


async def test_operation_scope_and_result_persist_after_reopen(
    harness: ScopeHarness,
    tmp_path: Path,
) -> None:
    harness.runtime.close()
    reopened = create_runtime(tmp_path / "scope")
    transport = httpx.ASGITransport(app=create_app(reopened.bus, auth=auth_service(reopened)))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            restored = ScopeHarness(
                reopened,
                client,
                harness.project,
                harness.tokens,
                harness.scopes,
                harness.target,
                harness.broad_target,
                harness.legacy_target,
            )
            before = restored.count()
            payload = {"project_id": restored.project, "operation_id": restored.target}
            denied = await restored.rpc("beta", "operation/result/read", "reopen-denied", payload)
            assert denied["error"]["data"]["reason_code"] == "AUTH_OPERATION_SCOPE_DENIED"
            assert restored.count() == before
            allowed = await restored.rpc(
                "alpha", "operation/result/read", "reopen-allowed", payload
            )
            assert allowed["result"]["value"]["state"] == "SUCCEEDED"
    finally:
        reopened.close()
