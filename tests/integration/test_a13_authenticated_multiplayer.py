from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import JsonValue
from sqlalchemy import func, select
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a10_storage_authority import source

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteAuthSessionStore, SqliteGovernanceStore
from thoth.adapters.storage.schema import operations
from thoth.application.services import LocalAuthenticationService
from thoth.domain.auth import AuthenticatedActorContext, IssuedAuthSession
from thoth.domain.canonical import head_set_digest
from thoth.protocol.jsonrpc import JsonRpcRequest


class DenyAllAuth:
    async def authenticate_http(
        self, *_args: object, **_kwargs: object
    ) -> AuthenticatedActorContext:
        raise PermissionError("AUTH_SESSION_REQUIRED")

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


def rpc_request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def rpc_value(response: object) -> dict[str, JsonValue]:
    from thoth.protocol.jsonrpc import JsonRpcResponse

    typed = cast(JsonRpcResponse, response)
    assert typed.error is None
    assert typed.result is not None
    value = typed.result["value"]
    assert isinstance(value, dict)
    return cast(dict[str, JsonValue], value)


@pytest.mark.asyncio
async def test_http_rpc_denies_unauthenticated_request_before_bus_io(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        with runtime.ledger.engine.connect() as connection:
            before = int(
                connection.execute(select(func.count()).select_from(operations)).scalar_one()
            )
        app = create_app(runtime.bus, auth=DenyAllAuth())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "id": "a13-denied",
                    "method": "project/read",
                    "params": {
                        "_meta": {"idempotencyKey": "a13-denied"},
                        "input": {"project_id": "project:a13"},
                    },
                },
            )
        assert response.status_code == 403
        assert response.json()["error"]["data"]["reason_code"] == "AUTH_SESSION_REQUIRED"
        with runtime.ledger.engine.connect() as connection:
            after = int(
                connection.execute(select(func.count()).select_from(operations)).scalar_one()
            )
        assert after == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_two_authenticated_scoped_actors_branch_merge_and_bind_revision_receipts(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "multiplayer")
    project_id = "project:a13:shared"
    other_project_id = "project:a13:other"
    alice = "human:a13:alice"
    bob = "human:a13:bob"
    scoped = "human:a13:scoped"
    credentials = {alice: "alice-secret", bob: "bob-secret", scoped: "scoped-secret"}
    try:
        for identifier in (project_id, other_project_id):
            rpc_value(
                await runtime.bus.dispatch(
                    rpc_request(
                        "project/create",
                        f"{identifier}:create",
                        {
                            "project_id": identifier,
                            "name": identifier,
                            "cutoff_at": "2026-09-01T00:00:00Z",
                        },
                    )
                )
            )
        role_ids: dict[str, str] = {}
        for actor_id, role, scope in (
            (alice, "project-owner", "PROJECT"),
            (bob, "independent-reviewer", "WORKSTREAM:beta"),
            (scoped, "scoped-researcher", "WORKSTREAM:alpha"),
        ):
            assigned = rpc_value(
                await runtime.bus.dispatch(
                    rpc_request(
                        "project/role/assign",
                        f"a13-role:{actor_id}",
                        {
                            "project_id": project_id,
                            "expected_revision": len(role_ids),
                            "actor_id": actor_id,
                            "role": role,
                            "scope": scope,
                            "authority_tags": [
                                "CAP_READ",
                                "CAP_WRITE",
                                "CAP_THREAD",
                                "CAP_REVISION",
                            ],
                        },
                    )
                )
            )
            role = cast(dict[str, JsonValue], assigned["role"])
            role_ids[actor_id] = str(role["role_assignment_id"])
        shared_source = await source(runtime, project_id, tmp_path / "multiplayer", "shared.md")
        receipt_subject = shared_source["artifact_id"]
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
        transport = httpx.ASGITransport(app=create_app(runtime.bus, auth=auth))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sessions: dict[str, dict[str, JsonValue]] = {}
            for actor in (alice, bob, scoped):
                response = await client.post(
                    "/auth/session",
                    json={
                        "actor_id": actor,
                        "project_id": project_id,
                        "role_assignment_id": role_ids[actor],
                    },
                    headers={"x-thoth-local-credential": credentials[actor]},
                )
                assert response.status_code == 200
                sessions[actor] = cast(dict[str, JsonValue], response.json())

            async def rpc(
                actor: str,
                method: str,
                key: str,
                input_value: dict[str, object],
                *,
                workstream: str | None = None,
            ) -> dict[str, JsonValue]:
                meta: dict[str, object] = {"idempotencyKey": key}
                if workstream is not None:
                    meta["dataScope"] = {"workstream": workstream}
                response = await client.post(
                    "/rpc",
                    json={
                        "jsonrpc": "2.0",
                        "id": key,
                        "method": method,
                        "params": {"_meta": meta, "input": input_value},
                    },
                    headers={"authorization": f"Bearer {sessions[actor]['bearer_token']}"},
                )
                assert response.status_code == 200, response.text
                body = cast(dict[str, object], response.json())
                assert "error" not in body, body.get("error")
                result = cast(dict[str, object], body["result"])
                child = result["value"]
                assert isinstance(child, dict)
                return cast(dict[str, JsonValue], child)

            listed = await rpc(
                alice,
                "project/list",
                "a13-project-list-scoped",
                {"project_id": project_id},
            )
            assert [
                cast(dict[str, JsonValue], project)["project_id"]
                for project in cast(list[JsonValue], listed["projects"])
            ] == [project_id]

            receipt_head_digest = head_set_digest(dict(runtime.ledger.read_heads(project_id)))
            sealed = cast(
                dict[str, JsonValue],
                (
                    await rpc(
                        alice,
                        "receipt/seal",
                        "a13-authenticated-receipt",
                        {
                            "project_id": project_id,
                            "receipt_type": "TRANSITION",
                            "claim_scopes": ["TRANSITION_RECORDED"],
                            "subject_refs": [receipt_subject],
                            "before_head_set_digest": receipt_head_digest,
                            "after_head_set_digest": receipt_head_digest,
                            "actor_or_agent_ref": alice,
                            "evidence_refs": [],
                            "policy_version": "policy:a13:receipt",
                        },
                    )
                )["receipt"],
            )
            alice_session = cast(dict[str, JsonValue], sessions[alice]["session"])
            assert sealed["actor_id"] == alice
            assert sealed["session_id"] == alice_session["session_id"]
            assert sealed["role_assignment_ref"] == role_ids[alice]
            verified = await rpc(
                alice,
                "receipt/verify",
                "a13-authenticated-receipt-verify",
                {"project_id": project_id, "receipt_id": sealed["receipt_id"]},
            )
            assert cast(dict[str, JsonValue], verified["verification"])["state"] == ("VERIFIED")
            corrected = await rpc(
                bob,
                "receipt/correction/create",
                "a13-authenticated-receipt-correction",
                {
                    "project_id": project_id,
                    "receipt_id": sealed["receipt_id"],
                    "correction_reason": "bind the correcting actor",
                    "corrected_subject_refs": [receipt_subject],
                },
                workstream="beta",
            )
            corrected_receipt = cast(dict[str, JsonValue], corrected["receipt"])
            assert corrected_receipt["actor_id"] == bob
            assert corrected_receipt["role_assignment_ref"] == role_ids[bob]

            before_spoof = len(runtime.ledger.read_receipts(project_id))
            spoofed = await client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "id": "a13-spoofed-receipt",
                    "method": "receipt/seal",
                    "params": {
                        "_meta": {"idempotencyKey": "a13-spoofed-receipt"},
                        "input": {
                            "project_id": project_id,
                            "receipt_type": "TRANSITION",
                            "claim_scopes": ["TRANSITION_RECORDED"],
                            "subject_refs": [receipt_subject],
                            "before_head_set_digest": receipt_head_digest,
                            "after_head_set_digest": receipt_head_digest,
                            "actor_or_agent_ref": bob,
                            "evidence_refs": [],
                            "policy_version": "policy:a13:receipt",
                        },
                    },
                },
                headers={"authorization": f"Bearer {sessions[alice]['bearer_token']}"},
            )
            assert spoofed.status_code == 200
            spoofed_body = cast(dict[str, JsonValue], spoofed.json())
            spoofed_error = cast(dict[str, JsonValue], spoofed_body["error"])
            assert spoofed_error["code"] == -32040
            assert len(runtime.ledger.read_receipts(project_id)) == before_spoof

            with runtime.ledger.engine.connect() as connection:
                before_denials = int(
                    connection.execute(select(func.count()).select_from(operations)).scalar_one()
                )
            cross_project = await client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "id": "a13-cross-project",
                    "method": "project/read",
                    "params": {
                        "_meta": {"idempotencyKey": "a13-cross-project"},
                        "input": {"project_id": other_project_id},
                    },
                },
                headers={"authorization": f"Bearer {sessions[alice]['bearer_token']}"},
            )
            assert cross_project.status_code == 403
            assert cross_project.json()["error"]["data"]["reason_code"] == (
                "AUTH_PROJECT_SCOPE_DENIED"
            )
            wrong_scope = await client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "id": "a13-wrong-scope",
                    "method": "thread/start",
                    "params": {
                        "_meta": {"idempotencyKey": "a13-wrong-scope"},
                        "input": {
                            "project_id": project_id,
                            "problem": "out of scoped workstream",
                            "scope": {"workstream": "beta"},
                        },
                    },
                },
                headers={"authorization": f"Bearer {sessions[scoped]['bearer_token']}"},
            )
            assert wrong_scope.status_code == 403
            assert wrong_scope.json()["error"]["data"]["reason_code"] == ("AUTH_DATA_SCOPE_DENIED")
            with runtime.ledger.engine.connect() as connection:
                after_denials = int(
                    connection.execute(select(func.count()).select_from(operations)).scalar_one()
                )
            assert after_denials == before_denials

            thread = await rpc(
                alice,
                "thread/start",
                "a13-thread",
                {
                    "project_id": project_id,
                    "thread_id": "thread:a13:shared",
                    "problem": "concurrently refine a shared object",
                    "scope": {"workstream": "alpha"},
                },
            )
            spoofed_steer = await client.post(
                "/rpc",
                json={
                    "id": "a13-spoofed-steer",
                    "method": "thread/steer",
                    "params": {
                        "_meta": {"idempotencyKey": "a13-spoofed-steer"},
                        "input": {
                            "project_id": project_id,
                            "thread_id": "thread:a13:shared",
                            "instruction": "spoof actor attribution",
                            "actor_id": bob,
                        },
                    },
                },
                headers={"authorization": f"Bearer {sessions[alice]['bearer_token']}"},
            )
            assert spoofed_steer.json()["error"]["data"]["reason_code"] == (
                "AUTH_ACTOR_IDENTITY_MISMATCH"
            )
            await rpc(
                alice,
                "thread/start",
                "a13-thread-beta",
                {
                    "project_id": project_id,
                    "thread_id": "thread:a13:beta",
                    "problem": "beta-only stored Thread",
                    "scope": {"workstream": "beta"},
                },
            )
            with runtime.ledger.engine.connect() as connection:
                before_forged_scope = int(
                    connection.execute(select(func.count()).select_from(operations)).scalar_one()
                )
            forged_scope = await client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "id": "a13-forged-stored-scope",
                    "method": "thread/read",
                    "params": {
                        "_meta": {
                            "idempotencyKey": "a13-forged-stored-scope",
                            "dataScope": {"workstream": "alpha"},
                        },
                        "input": {
                            "project_id": project_id,
                            "thread_id": "thread:a13:beta",
                        },
                    },
                },
                headers={"authorization": f"Bearer {sessions[scoped]['bearer_token']}"},
            )
            assert forged_scope.status_code == 200
            assert forged_scope.json()["error"]["code"] == -32040
            assert forged_scope.json()["error"]["data"]["reason_code"] == ("AUTH_DATA_SCOPE_DENIED")
            with runtime.ledger.engine.connect() as connection:
                after_forged_scope = int(
                    connection.execute(select(func.count()).select_from(operations)).scalar_one()
                )
            assert after_forged_scope == before_forged_scope
            object_id = str(cast(list[JsonValue], thread["current_object_ids"])[0])
            aggregate_key = f"DECISION_OBJECT:{object_id}"
            evidence = await rpc(
                alice, "evidence/list", "a13-shared-evidence", {"project_id": project_id}
            )
            shared_refs = [
                str(span["span_id"]) for span in cast(list[dict[str, JsonValue]], evidence["spans"])
            ]
            # The creator incorporates the private draft into a source-bound record
            # before the second actor proposes a concurrent revision.
            await rpc(
                alice,
                "object/frame/revise",
                "a13-publish-source-bound-frame",
                {
                    "project_id": project_id,
                    "object_id": object_id,
                    "expected_revision_digest": runtime.ledger.read_heads(project_id)[
                        aggregate_key
                    ],
                    "frame_patch": {"purpose_statement": "shared source comparison"},
                    "evidence_refs": shared_refs,
                    "reason": "incorporate the draft into the shared research record",
                },
                workstream="alpha",
            )
            heads = dict(runtime.ledger.read_heads(project_id))
            base = heads[aggregate_key]
            base_revision = runtime.ledger.read_revision_by_digest(project_id, base)
            assert base_revision is not None
            base_snapshot = runtime.ledger.read_snapshot(base_revision.snapshot_id)
            assert base_snapshot is not None

            proposals: dict[str, dict[str, JsonValue]] = {}
            for actor, workstream, field, content in (
                (alice, "alpha", "purpose_statement", "alice purpose"),
                (bob, "beta", "problem_frame", "bob problem frame"),
            ):
                proposals[actor] = cast(
                    dict[str, JsonValue],
                    (
                        await rpc(
                            actor,
                            "revision/propose",
                            f"a13-propose:{actor}",
                            {
                                "project_id": project_id,
                                "aggregate_id": object_id,
                                "aggregate_type": "DECISION_OBJECT",
                                "parent_revision_digests": [base],
                                "candidate_content": {
                                    **base_snapshot.content,
                                    field: content,
                                },
                                "reason": "independent authenticated edit",
                                "evidence_refs": [],
                                "actor_or_agent_ref": actor,
                                "expected_head_digest": base,
                            },
                            workstream=workstream,
                        )
                    )["proposal"],
                )

            async def commit_actor(actor: str, workstream: str) -> dict[str, JsonValue]:
                created = await rpc(
                    actor,
                    "revision/changeSet/create",
                    f"a13-changeset:{actor}",
                    {
                        "project_id": project_id,
                        "expected_head_set": heads,
                        "candidate_revision_digests": [proposals[actor]["record_digest"]],
                        "transition_reason": "authenticated concurrent revision",
                        "impact_policy_ref": "merge:three-way-v1",
                    },
                    workstream=workstream,
                )
                change_set = cast(dict[str, JsonValue], created["change_set"])
                validated = await rpc(
                    actor,
                    "revision/changeSet/validate",
                    f"a13-validate:{actor}",
                    {
                        "project_id": project_id,
                        "record_id": change_set["record_id"],
                        "expected_change_set_revision": change_set["version"],
                    },
                    workstream=workstream,
                )
                return await rpc(
                    actor,
                    "revision/changeSet/commit",
                    f"a13-commit:{actor}",
                    {
                        "project_id": project_id,
                        "record_id": change_set["record_id"],
                        "validation_bundle_digest": validated["validation_bundle_digest"],
                        "expected_head_set_digest": head_set_digest(heads),
                    },
                    workstream=workstream,
                )

            alice_commit = await commit_actor(alice, "alpha")
            left = cast(dict[str, str], alice_commit["new_project_head_set"])[aggregate_key]
            bob_commit = await commit_actor(bob, "beta")
            right = cast(list[str], bob_commit["branch_revision_digests"])[0]
            bob_receipt = cast(dict[str, JsonValue], bob_commit["receipt"])
            assert bob_receipt["actor_id"] == bob
            assert (
                bob_receipt["session_id"]
                == cast(dict[str, JsonValue], sessions[bob]["session"])["session_id"]
            )
            right_revision = runtime.ledger.read_revision_by_digest(project_id, right)
            assert right_revision is not None
            assert right_revision.actor.actor_id == bob
            assert right_revision.actor.session_id == bob_receipt["session_id"]
            assert runtime.ledger.read_heads(project_id)[aggregate_key] == left

            merged = await rpc(
                alice,
                "revision/merge/propose",
                "a13-merge",
                {
                    "project_id": project_id,
                    "aggregate_id": object_id,
                    "parent_revision_digests": [left, right],
                    "merge_policy_ref": "merge:three-way-v1",
                    "reason": "merge authenticated independent changes",
                    "evidence_refs": [],
                },
            )
            semantic_merge = cast(dict[str, JsonValue], merged["semantic_merge"])
            assert semantic_merge["state"] == "AUTO_MERGED"
            merged_revision = runtime.ledger.read_revision_by_digest(
                project_id, str(semantic_merge["merged_revision_digest"])
            )
            assert merged_revision is not None
            assert merged_revision.actor.actor_id == alice
            assert (
                merged_revision.actor.session_id
                == cast(dict[str, JsonValue], sessions[alice]["session"])["session_id"]
            )
            assert "approval" not in str(merged).casefold()

            current_project = rpc_value(
                await runtime.bus.dispatch(
                    rpc_request(
                        "project/read",
                        "a13-project-before-revoke",
                        {"project_id": project_id},
                    )
                )
            )
            rpc_value(
                await runtime.bus.dispatch(
                    rpc_request(
                        "project/role/revoke",
                        "a13-revoke-bob",
                        {
                            "project_id": project_id,
                            "expected_revision": current_project["revision"],
                            "role_assignment_id": role_ids[bob],
                        },
                    )
                )
            )
            revoked = await client.post(
                "/rpc",
                json={
                    "id": "a13-revoked-session",
                    "method": "project/read",
                    "params": {
                        "_meta": {
                            "idempotencyKey": "a13-revoked-session",
                            "dataScope": {"workstream": "beta"},
                        },
                        "input": {"project_id": project_id},
                    },
                },
                headers={"authorization": f"Bearer {sessions[bob]['bearer_token']}"},
            )
            assert revoked.status_code == 403
            assert revoked.json()["error"]["data"]["reason_code"] == ("AUTH_ROLE_BINDING_INVALID")
    finally:
        runtime.close()
