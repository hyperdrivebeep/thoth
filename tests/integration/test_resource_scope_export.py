"""A stored local export snapshot never substitutes for current source access."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import denial, scope_harness, value

from thoth.domain.canonical import head_set_digest


async def test_semantic_export_list_follows_source_and_receipt_access(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        resource = value(
            await h.connect(
                "alpha",
                "semantic-export-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )["artifact"]["artifact_id"]
        head = head_set_digest(dict(h.runtime.ledger.read_heads(h.project)))
        sealed = value(
            await h.call(
                "alpha",
                "receipt/seal",
                "semantic-source-receipt",
                {
                    "receipt_type": "TRANSITION",
                    "claim_scopes": ["TRANSITION_RECORDED"],
                    "subject_refs": [resource],
                    "evidence_refs": [],
                    "before_head_set_digest": head,
                    "after_head_set_digest": head,
                    "actor_or_agent_ref": h.actors["alpha"],
                    "policy_version": "local-review",
                },
            )
        )["receipt"]
        prepared = value(
            await h.call(
                "alpha",
                "export/prepare",
                "semantic-export",
                {
                    "purpose": "local source review",
                    "audience": "authorized reviewer",
                },
            )
        )["export"]
        assert sealed["receipt_id"] in prepared["receipt_refs"]
        identifier = prepared["export_id"]
        assert identifier in str(value(await h.call("alpha", "export/list", "owner-list", {})))
        assert identifier not in str(value(await h.call("beta", "export/list", "before-grant", {})))
        current = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "read-scope",
                {
                    "resource_ref": resource,
                },
            )
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant-scope",
                {
                    "resource_ref": resource,
                    "expected_revision": current["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "local review",
                },
            )
        )["scope"]
        assert identifier in str(value(await h.call("beta", "export/list", "after-grant", {})))
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke-scope",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "review ended",
                },
            )
        )
        assert identifier not in str(value(await h.call("beta", "export/list", "after-revoke", {})))


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["export/plan/read", "export/snapshot/read", "export/list"])
async def test_export_reads_recheck_current_source_scope(tmp_path: Path, surface: str) -> None:
    async with scope_harness(tmp_path) as h:
        resource = value(
            await h.connect(
                "alpha",
                "export-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha", "project/source/scope/read", "before-export", {"resource_ref": resource}
            )
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "export-access",
                {
                    "resource_ref": resource,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "local review export",
                },
            )
        )["scope"]
        plan = value(
            await h.call(
                "beta",
                "export/plan/create",
                "scope-export-plan",
                {
                    "purpose": "REVIEW_HANDOFF",
                    "recipient": "local-review",
                    "trust_boundary": "LOCAL_ONLY",
                    "scope_refs": [resource],
                    "cutoff": "2026-09-01T00:00:00Z",
                    "head_set": dict(h.runtime.ledger.read_heads(h.project)),
                    "selection_rules": {},
                    "classification_ceiling": "INTERNAL",
                    "rights_policy_ref": "local-review-rights",
                    "privacy_policy_ref": "local-review-privacy",
                    "renderers": ["JSON"],
                },
            )
        )["export_plan"]
        snapshot = value(
            await h.call(
                "beta",
                "export/snapshot/create",
                "scope-export-snapshot",
                {
                    "export_plan_id": plan["record_id"],
                    "expected_plan_revision": plan["version"],
                },
            )
        )["export_snapshot"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "withdraw-export",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "end export access",
                },
            )
        )
        target = plan if surface == "export/plan/read" else snapshot
        payload = {} if surface == "export/list" else {"export_id": target["record_id"]}
        response = await h.call("beta", surface, "after-withdrawal", payload)
        if surface == "export/list":
            assert resource not in str(value(response))
        else:
            denial(response, "RESOURCE_ACCESS_DENIED")
