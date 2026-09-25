"""An executed sandbox result survives source revocation without becoming active evidence."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_a04_r2_closed_loop import A04R2Model, policy_payload

from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.adapters.storage import SqliteControlRecordStore
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate
from thoth.domain.sandbox import SandboxResult, SandboxRunSpec


async def test_admitted_sandbox_receipt_nodes_share_with_all_input_sources(tmp_path: Path) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            "alpha": ResourceScopeTemplate(
                owner_kind="WORKSTREAM",
                owner_workstream="alpha",
                visibility="WORKSTREAM",
            )
        }
    )
    sandbox = ScriptedSandboxAdapter()
    async with scope_harness(
        tmp_path, policy, model_resolver=StaticModelResolver(A04R2Model()), sandbox_adapter=sandbox
    ) as h:
        project = value(await h.call("owner", "project/read", "share-project", {}))
        payload = policy_payload(allow_sandbox=True)
        payload["connector_allowlist"] = ["local-file-upload"]
        payload["resource_scope_policy"] = policy.model_dump(mode="json")
        value(
            await h.call(
                "owner",
                "project/policy/update",
                "share-policy",
                {
                    "expected_revision": project["revision"],
                    "payload": payload,
                },
            )
        )
        resource = value(await h.connect("alpha", "share-source", None))["artifact"]["artifact_id"]
        value(
            await h.call(
                "alpha",
                "thread/start",
                "share-thread",
                {
                    "thread_id": "thread:alpha:sandbox-sharing",
                    "problem": "Run the next safe R2 check.",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        value(
            await h.call(
                "alpha",
                "thread/input",
                "share-cycle",
                {
                    "thread_id": "thread:alpha:sandbox-sharing",
                },
            )
        )
        assert len(sandbox.seen_specs) == 1
        own = value(await h.call("alpha", "receipt/audit/read", "own-sandbox-dag", {}))["dag"]
        sandbox_ids = {node["node_id"] for node in own["nodes"] if node["kind"] == "SANDBOX"}
        assert sandbox_ids
        current = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "share-scope",
                {
                    "resource_ref": resource,
                },
            )
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "share-grant",
                {
                    "resource_ref": resource,
                    "expected_revision": current["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "review sandbox evidence",
                },
            )
        )["scope"]
        shared = value(await h.call("beta", "receipt/audit/read", "shared-sandbox-dag", {}))["dag"]
        assert sandbox_ids <= {node["node_id"] for node in shared["nodes"]}
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "share-revoke",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "review ended",
                },
            )
        )
        revoked = value(await h.call("beta", "receipt/audit/read", "revoked-sandbox-dag", {}))[
            "dag"
        ]
        assert not sandbox_ids & {node["node_id"] for node in revoked["nodes"]}


@pytest.mark.asyncio
async def test_executed_result_is_captured_when_source_is_revoked_during_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            "alpha": ResourceScopeTemplate(
                owner_kind="WORKSTREAM",
                owner_workstream="alpha",
                visibility="WORKSTREAM",
            )
        }
    )
    sandbox = ScriptedSandboxAdapter()
    original = ScriptedSandboxAdapter.run
    async with scope_harness(
        tmp_path, policy, model_resolver=StaticModelResolver(A04R2Model()), sandbox_adapter=sandbox
    ) as h:
        project = value(await h.call("owner", "project/read", "sandbox-project", {}))
        payload = policy_payload(allow_sandbox=True)
        payload["connector_allowlist"] = ["local-file-upload"]
        payload["resource_scope_policy"] = policy.model_dump(mode="json")
        value(
            await h.call(
                "owner",
                "project/policy/update",
                "sandbox-policy",
                {
                    "expected_revision": project["revision"],
                    "payload": payload,
                },
            )
        )
        source = value(await h.connect("alpha", "sandbox-source", None))["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "sandbox-scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        shared = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "sandbox-grant",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "allow sandbox input",
                },
            )
        )["scope"]

        async def withdraw(self: ScriptedSandboxAdapter, spec: SandboxRunSpec) -> SandboxResult:
            result = await original(self, spec)
            value(
                await h.call(
                    "alpha",
                    "project/source/scope/revoke",
                    "sandbox-revoke",
                    {
                        "resource_ref": source,
                        "expected_revision": shared["revision"],
                        "grant_id": shared["grants"][0]["grant_id"],
                        "reason": "withdraw after execution",
                    },
                )
            )
            return result

        monkeypatch.setattr(ScriptedSandboxAdapter, "run", withdraw)
        value(
            await h.call(
                "beta",
                "thread/start",
                "sandbox-thread",
                {
                    "thread_id": "thread:beta:sandbox",
                    "problem": "Run the next safe R2 check.",
                    "scope": {"workstream": "beta"},
                },
            )
        )
        response = await h.call(
            "beta",
            "thread/input",
            "sandbox-cycle",
            {
                "thread_id": "thread:beta:sandbox",
            },
        )
        assert len(sandbox.seen_specs) == 1, response.text
        controls = SqliteControlRecordStore(h.runtime.ledger.engine)
        receipts = controls.list(h.project, "SANDBOX", "RECEIPT")
        assert len(receipts) == 1, "the real process outcome was lost after source revocation"
        runs = controls.list(h.project, "SANDBOX", "RUN")
        assert runs[-1].payload["evidence_admission"] == "CAPTURED_NOT_ADMITTED"
        assert runs[-1].payload["resource_parent_refs"]
        assert "error" in response.json(), response.text
        again = await h.call(
            "beta",
            "thread/input",
            "sandbox-cycle",
            {
                "thread_id": "thread:beta:sandbox",
            },
        )
        assert "error" in again.json()
        assert len(sandbox.seen_specs) == 1
