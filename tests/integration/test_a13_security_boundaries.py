from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.test_a02_autonomous_acquisition import request, value

from thoth.adapters.http.app import create_app
from thoth.adapters.storage.auth import SqliteAuthSessionStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.apps.runtime import create_runtime
from thoth.domain.auth import (
    AuthenticatedActorContext,
    AuthSession,
    IssuedAuthSession,
    authenticated_actor_scope,
)
from thoth.domain.governance import RoleAssignment
from thoth.domain.upload_scope import project_upload_path_allowed, project_upload_prefix


class StaticProjectAuth:
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


def context(project_id: str) -> AuthenticatedActorContext:
    suffix = project_id.rsplit(":", 1)[-1]
    return AuthenticatedActorContext(
        actor_id=f"human:{suffix}",
        session_id=f"session:{suffix}",
        project_id=project_id,
        role_assignment_id=f"role:{suffix}",
        role="project-owner",
        capabilities=("ADMIN", "READ", "WRITE"),
        data_scopes=("PROJECT",),
    )


def policy() -> dict[str, object]:
    return {
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": ["local-file-upload"],
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": [],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
    }


@pytest.mark.asyncio
async def test_local_loopback_rejects_protected_actor_ref_override(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "local-identity")
    application = create_app(runtime.bus)
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/rpc",
                json={
                    "id": "a13-local-authority-spoof",
                    "method": "revision/baseline/decide",
                    "params": {
                        "_meta": {"idempotencyKey": "a13-local-authority-spoof"},
                        "input": {
                            "project_id": "project:a13",
                            "actor_ref": "human:project-owner",
                            "role_assignment_ref": "role:project-owner",
                        },
                    },
                },
            )
        payload = response.json()
    finally:
        runtime.close()
    assert payload["error"]["data"] == {
        "reason_code": "LOCAL_ACTOR_OVERRIDE_DENIED",
        "expected_actor_id": "human:local-user",
        "pre_io": True,
    }


def test_project_upload_path_rejects_parent_traversal() -> None:
    project_a = "project:a13:upload-a"
    project_b = "project:a13:upload-b"
    escaped = (
        f"{project_upload_prefix(project_a)}/../"
        f"{project_upload_prefix(project_b).rsplit('/', 1)[-1]}/secret.md"
    )
    assert project_upload_path_allowed(project_a, escaped) is False


@pytest.mark.asyncio
async def test_authenticated_upload_path_is_project_scoped_at_stage_and_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    runtime = create_runtime(workspace)
    project_a = "project:a13:upload-a"
    project_b = "project:a13:upload-b"
    context_a = context(project_a)
    context_b = context(project_b)
    try:
        for project_id in (project_a, project_b):
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/create",
                        f"{project_id}:create",
                        {
                            "project_id": project_id,
                            "name": project_id,
                            "cutoff_at": "2026-09-04T00:00:00Z",
                        },
                    )
                )
            )
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/policy/update",
                        f"{project_id}:policy",
                        {
                            "project_id": project_id,
                            "expected_revision": 0,
                            "payload": policy(),
                        },
                    )
                )
            )
        now = datetime.now(UTC)
        for actor in (context_a, context_b):
            SqliteGovernanceStore(runtime.ledger.engine).create_role(
                RoleAssignment(
                    role_assignment_id=actor.role_assignment_id,
                    project_id=actor.project_id,
                    actor_id=actor.actor_id,
                    role=actor.role,
                    scope="PROJECT",
                    authority_tags=tuple(f"CAP_{cap}" for cap in actor.capabilities),
                    created_at=now,
                )
            )
            SqliteAuthSessionStore(runtime.ledger.engine).put(
                AuthSession(
                    session_id=actor.session_id,
                    actor_id=actor.actor_id,
                    project_id=actor.project_id,
                    role_assignment_id=actor.role_assignment_id,
                    role=actor.role,
                    capabilities=actor.capabilities,
                    data_scopes=actor.data_scopes,
                    token_digest=hashlib.sha256(actor.session_id.encode()).hexdigest(),
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                )
            )
        application = create_app(
            runtime.bus,
            auth=StaticProjectAuth({"token-a": context_a, "token-b": context_b}),
        )
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            staged = await client.post(
                "/files/stage",
                files={"file": ("plan.md", b"# project A\n", "text/markdown")},
                headers={
                    "authorization": "Bearer token-a",
                    "x-thoth-project-id": project_a,
                },
            )
        assert staged.status_code == 200
        relative_path = str(staged.json()["relative_path"])
        assert relative_path.startswith("web/projects/")

        with authenticated_actor_scope(context_b):
            denied = await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "a13-upload-cross-project",
                    {
                        "project_id": project_b,
                        "connector_id": "local-file-upload",
                        "selector": {"relative_path": relative_path},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        assert denied.error is not None
        assert denied.error.code == -32040
        unauthenticated_denied = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "a13-upload-cross-project-unauthenticated",
                {
                    "project_id": project_b,
                    "connector_id": "local-file-upload",
                    "selector": {"relative_path": relative_path},
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        assert unauthenticated_denied.error is not None
        assert unauthenticated_denied.error.code == -32040
        with authenticated_actor_scope(context_a):
            connected = value(
                await runtime.bus.dispatch(
                    request(
                        "project/source/connect",
                        "a13-upload-same-project",
                        {
                            "project_id": project_a,
                            "connector_id": "local-file-upload",
                            "selector": {"relative_path": relative_path},
                            "media_type": "text/markdown",
                            "authority": "OFFICIAL",
                            "cutoff_state": "ELIGIBLE",
                            "security_class": "INTERNAL",
                        },
                    )
                )
            )
        assert cast(dict[str, JsonValue], connected["artifact"])["project_id"] == project_a
    finally:
        runtime.close()
