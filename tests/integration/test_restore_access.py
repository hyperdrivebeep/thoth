from hashlib import sha256
from pathlib import Path

import httpx
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_restore_apply_atomicity import CHANGES, handler
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteAuthSessionStore, SqliteGovernanceStore
from thoth.application.services import LocalAuthenticationService


async def test_read_only_principal_gets_real_preview_without_writes_and_cannot_apply(
    tmp_path: Path,
):
    runtime, _model, _accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snap, *CHANGES["hypothesis.v1"])
        # Authenticate the original local draft owner with READ-only capabilities.
        # A different principal cannot acquire source-free drafts merely by reading the project.
        actor = "local:operator"
        project = host.planner.projects.read("p")
        assert project is not None
        role = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "reader-role",
                    {
                        "project_id": "p",
                        "expected_revision": project.revision,
                        "actor_id": actor,
                        "role": "history-reader",
                        "scope": "PROJECT",
                        "authority_tags": ["CAP_READ", "CAP_REVISION"],
                    },
                )
            )
        )["role"]
        auth = LocalAuthenticationService(
            sessions=SqliteAuthSessionStore(runtime.ledger.engine),
            governance=SqliteGovernanceStore(runtime.ledger.engine),
            credentials=StaticCredentialVerifier({actor: sha256(b"fixture-only").hexdigest()}),
            tokens=SecureSessionTokenIssuer(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus, auth=auth)),
            base_url="http://test",
        ) as client:
            issued = await client.post(
                "/auth/session",
                json={
                    "actor_id": actor,
                    "project_id": "p",
                    "role_assignment_id": role["role_assignment_id"],
                },
                headers={"x-thoth-local-credential": "fixture-only"},
            )
            assert issued.status_code == 200
            headers = {"authorization": "Bearer " + issued.json()["bearer_token"]}
            selection = {
                "project_id": "p",
                "entity_type": "HYPOTHESIS",
                "entity_id": revision.entity_id,
                "target_revision_digest": revision.revision_digest,
                "expected_current_head": changed.revision_digest,
            }
            payload: dict[str, object] = {
                "project_id": "p",
                "selection": selection,
                "contract_version": 2,
            }
            before = snapshot(runtime.ledger.engine)
            response = await client.post(
                "/rpc/query",
                json=request("revision/restore/preview", "preview", payload).model_dump(
                    mode="json", by_alias=True
                ),
                headers=headers,
            )
            body = response.json()
            assert "error" not in body, body
            preview = body["result"]["value"]
            assert preview["availability"] == "AVAILABLE" and preview["basis_digest"]
            assert preview["capability"]["preview_supported"]
            assert not preview["capability"]["apply_ready"]
            assert preview["capability"]["reason_codes"] == ["RESTORE_ACCESS_DENIED"]
            denied = await client.post(
                "/rpc",
                json=request(
                    "revision/restore/apply",
                    "apply",
                    {
                        **payload,
                        "preview_basis_digest": preview["basis_digest"],
                        "reason": "must deny",
                    },
                ).model_dump(mode="json", by_alias=True),
                headers=headers,
            )
            assert "error" in denied.json()
            assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()
