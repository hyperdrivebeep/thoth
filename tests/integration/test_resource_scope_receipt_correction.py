"""Receipt corrections inherit both original and corrected source permissions."""

from pathlib import Path

from tests.integration.resource_scope_helpers import scope_harness, value

from thoth.domain.canonical import head_set_digest


async def test_receipt_correction_rechecks_original_source_after_revocation(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        resources = {}
        for actor in ("alpha", "beta"):
            resources[actor] = value(
                await h.connect(
                    actor,
                    f"correction-{actor}",
                    {
                        "owner_kind": "WORKSTREAM",
                        "owner_workstream": actor,
                        "visibility": "WORKSTREAM",
                    },
                )
            )["artifact"]["artifact_id"]
        current = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "scope",
                {
                    "resource_ref": resources["alpha"],
                },
            )
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant",
                {
                    "resource_ref": resources["alpha"],
                    "expected_revision": current["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "review",
                },
            )
        )["scope"]
        head = head_set_digest(dict(h.runtime.ledger.read_heads(h.project)))
        sealed = value(
            await h.call(
                "alpha",
                "receipt/seal",
                "seal",
                {
                    "receipt_type": "TRANSITION",
                    "claim_scopes": ["TRANSITION_RECORDED"],
                    "subject_refs": [resources["alpha"]],
                    "evidence_refs": [],
                    "before_head_set_digest": head,
                    "after_head_set_digest": head,
                    "actor_or_agent_ref": h.actors["alpha"],
                    "policy_version": "local-review",
                },
            )
        )["receipt"]
        corrected = value(
            await h.call(
                "beta",
                "receipt/correction/create",
                "correct",
                {
                    "receipt_id": sealed["receipt_id"],
                    "correction_reason": "private correction marker",
                    "corrected_subject_refs": [resources["beta"]],
                },
            )
        )["receipt"]
        value(
            await h.call(
                "beta",
                "receipt/read",
                "before-revoke",
                {
                    "receipt_id": corrected["receipt_id"],
                },
            )
        )
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke",
                {
                    "resource_ref": resources["alpha"],
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "review ended",
                },
            )
        )
        denied = await h.call(
            "beta",
            "receipt/read",
            "after-revoke",
            {
                "receipt_id": corrected["receipt_id"],
            },
        )
        assert "error" in denied.json()
        repeated = await h.call(
            "beta",
            "receipt/correction/create",
            "repeat",
            {
                "receipt_id": sealed["receipt_id"],
                "correction_reason": "second correction",
                "corrected_subject_refs": [resources["beta"]],
            },
        )
        assert "error" in repeated.json()
        # Collection queries must also omit correction control metadata.
        audit = value(await h.call("beta", "receipt/audit/read", "audit", {}))
        assert corrected["receipt_id"] not in str(audit)
        assert "private correction marker" not in str(audit)
