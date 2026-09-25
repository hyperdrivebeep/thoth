"""Real local HTTP sessions for resource-owner acceptance scenarios."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from tests.integration.storage_coverage_helpers import request
from tests.integration.storage_coverage_helpers import value as rpc_value

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteAuthSessionStore, SqliteGovernanceStore
from thoth.application.services import LocalAuthenticationService
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.resource_scope import ResourceScopePolicy
from thoth.domain.upload_scope import project_upload_prefix
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.sandbox import SandboxPort


def value(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = cast(dict[str, Any], response.json())
    assert "error" not in body, body
    return cast(dict[str, Any], body["result"]["value"])


def denial(response: httpx.Response, reason: str) -> None:
    body = cast(dict[str, Any], response.json())
    assert "error" in body, body
    assert body["error"].get("data", {}).get("reason_code") == reason, body


@dataclass
class ScopeHarness:
    workspace: Path
    runtime: AppRuntime
    client: httpx.AsyncClient
    project: str
    actors: dict[str, str]
    tokens: dict[str, str]

    async def call(
        self, actor: str, method: str, key: str, payload: dict[str, object]
    ) -> httpx.Response:
        meta: dict[str, object] = {"idempotencyKey": key}
        if actor != "owner":
            meta["dataScope"] = {"workstream": actor}
        return await self.client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": key,
                "method": method,
                "params": {"_meta": meta, "input": {"project_id": self.project, **payload}},
            },
            headers={"authorization": f"Bearer {self.tokens[actor]}"},
        )

    async def connect(
        self, actor: str, key: str, scope: dict[str, object] | None
    ) -> httpx.Response:
        relative = f"{project_upload_prefix(self.project)}/{key}.md"
        path = self.workspace / "inbox" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Trial\n\n{actor}-private-marker\n", encoding="utf-8")
        payload: dict[str, object] = {
            "relative_path": relative,
            "media_type": "text/markdown",
            "authority": "INFORMAL",
            "cutoff_state": "ELIGIBLE",
            "security_class": "INTERNAL",
        }
        if scope is not None:
            payload["resource_scope"] = scope
        return await self.call(actor, "project/source/connect", key, payload)


@asynccontextmanager
async def scope_peer(original: ScopeHarness) -> AsyncGenerator[ScopeHarness]:
    runtime = create_runtime(original.workspace)
    try:
        auth = LocalAuthenticationService(
            sessions=SqliteAuthSessionStore(runtime.ledger.engine),
            governance=SqliteGovernanceStore(runtime.ledger.engine),
            credentials=StaticCredentialVerifier(
                {
                    actor: hashlib.sha256(f"fixture-credential:{name}".encode()).hexdigest()
                    for name, actor in original.actors.items()
                }
            ),
            tokens=SecureSessionTokenIssuer(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus, auth=auth)),
            base_url="http://test",
        ) as client:
            yield ScopeHarness(
                original.workspace,
                runtime,
                client,
                original.project,
                original.actors,
                original.tokens,
            )
    finally:
        runtime.close()


@asynccontextmanager
async def scope_harness(
    workspace: Path,
    policy: ResourceScopePolicy | None = None,
    *,
    model_resolver: ModelResolverPort | None = None,
    sandbox_adapter: SandboxPort | None = None,
    owner_admin: bool = True,
    evaluation_catalog: EvaluationCatalogPort | None = None,
) -> AsyncGenerator[ScopeHarness]:
    runtime = create_runtime(
        workspace,
        resource_scope_policy=policy,
        model_resolver=model_resolver,
        sandbox_adapter=sandbox_adapter,
        evaluation_catalog=evaluation_catalog,
    )
    project = "project:resource-scope"
    actors = {name: f"human:resource:{name}" for name in ("owner", "alpha", "beta")}
    credentials = {actor: f"fixture-credential:{name}" for name, actor in actors.items()}
    try:
        rpc_value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "scope-project",
                    {
                        "project_id": project,
                        "name": "Scoped trial",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        roles: dict[str, str] = {}
        for ordinal, (name, actor) in enumerate(actors.items()):
            tags = [
                "CAP_READ",
                "CAP_WRITE",
                "CAP_THREAD",
                "CAP_REVISION",
                "CAP_RESOURCE_SCOPE_WRITE",
            ]
            if name == "owner" and owner_admin:
                tags.append("CAP_ADMIN")
            role = rpc_value(
                await runtime.bus.dispatch(
                    request(
                        "project/role/assign",
                        f"scope-role:{name}",
                        {
                            "project_id": project,
                            "expected_revision": ordinal,
                            "actor_id": actor,
                            "role": "resource-operator",
                            "scope": "PROJECT" if name == "owner" else f"WORKSTREAM:{name}",
                            "authority_tags": tags,
                        },
                    )
                )
            )["role"]
            roles[name] = role["role_assignment_id"]
        auth = LocalAuthenticationService(
            sessions=SqliteAuthSessionStore(runtime.ledger.engine),
            governance=SqliteGovernanceStore(runtime.ledger.engine),
            credentials=StaticCredentialVerifier(
                {
                    actor: hashlib.sha256(secret.encode()).hexdigest()
                    for actor, secret in credentials.items()
                }
            ),
            tokens=SecureSessionTokenIssuer(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus, auth=auth)),
            base_url="http://test",
        ) as client:
            tokens: dict[str, str] = {}
            for name, actor in actors.items():
                response = await client.post(
                    "/auth/session",
                    json={
                        "actor_id": actor,
                        "project_id": project,
                        "role_assignment_id": roles[name],
                    },
                    headers={"x-thoth-local-credential": credentials[actor]},
                )
                assert response.status_code == 200
                tokens[name] = str(response.json()["bearer_token"])
            yield ScopeHarness(workspace, runtime, client, project, actors, tokens)
    finally:
        runtime.close()
