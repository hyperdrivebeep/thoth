from pathlib import Path

from tests.integration.resource_scope_helpers import scope_harness, value

from thoth.protocol.jsonrpc import RpcErrorCode


async def test_explicit_revalidation_does_not_bypass_revoked_source_grant(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        source = value(
            await h.connect(
                "alpha",
                "source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )
        resource = source["artifact"]["artifact_id"]
        current = value(
            await h.call("alpha", "project/source/scope/read", "scope", {"resource_ref": resource})
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant",
                {
                    "resource_ref": resource,
                    "expected_revision": current["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "Review current evidence",
                },
            )
        )["scope"]
        spans = value(await h.call("beta", "evidence/list", "spans", {}))["spans"]
        span = next(s for s in spans if s["artifact_id"] == resource)
        link = value(
            await h.call(
                "beta",
                "evidence/link/propose",
                "link",
                {
                    "target_type": "DECISION_OBJECT",
                    "target_id": "object:review-candidate",
                    "relation": "SUPPORTS",
                    "span_ids": [span["span_id"]],
                    "observed_statement": "Controlled source observation",
                    "independence_group": "scoped-fixture",
                },
            )
        )["evidence"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "Review access withdrawn",
                },
            )
        )
        response = await h.call(
            "beta", "evidence/revalidate", "revalidate", {"evidence_id": link["evidence_id"]}
        )
        error = response.json()["error"]
        assert error["code"] == RpcErrorCode.DOMAIN_REJECTED
        assert error["message"] == "RESOURCE_ACCESS_DENIED"
        assert "alpha-private-marker" not in response.text
