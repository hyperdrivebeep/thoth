"""A mixed evidence result cannot outlive access to any of its source parents."""

from pathlib import Path
from typing import Any

import pytest
from tests.integration.resource_scope_helpers import denial, scope_harness, value

from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
async def test_b_all_sources_granted_exposes_derived_record_without_owner_grant(
    tmp_path: Path,
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
    async with scope_harness(tmp_path, policy) as h:
        resources = [
            value(await h.connect("alpha", f"parent-{i}", None))["artifact"]["artifact_id"]
            for i in range(2)
        ]
        spans = value(await h.call("alpha", "evidence/list", "b-parent-spans", {}))["spans"]
        span_ids = [
            next(
                s["span_id"]
                for s in spans
                if s["artifact_id"] == ref and "private-marker" in s["exact_text"]
            )
            for ref in resources
        ]
        link = value(
            await h.call(
                "alpha",
                "evidence/link/propose",
                "b-derived",
                {
                    "target_type": "DECISION_OBJECT",
                    "target_id": "alpha-private-question",
                    "relation": "QUALIFIES",
                    "span_ids": span_ids,
                    "observed_statement": "alpha interpretation and draft",
                    "independence_group": "b-policy-trial",
                },
            )
        )["evidence"]
        conflict = value(
            await h.call(
                "alpha",
                "evidence/challenge",
                "b-derived-question",
                {
                    "evidence_ids": [link["evidence_id"]],
                    "field": "interpretation",
                    "reason": "alpha-question-draft-marker",
                },
            )
        )["conflict"]
        grants: list[dict[str, Any]] = []
        for ordinal, ref in enumerate(resources):
            before = value(
                await h.call(
                    "alpha",
                    "project/source/scope/read",
                    f"b-scope-{ordinal}",
                    {"resource_ref": ref},
                )
            )["scope"]
            granted = value(
                await h.call(
                    "alpha",
                    "project/source/scope/grant",
                    f"b-grant-{ordinal}",
                    {
                        "resource_ref": ref,
                        "expected_revision": before["revision"],
                        "grantee_kind": "ACTOR",
                        "grantee_ref": h.actors["beta"],
                        "reason": "share underlying source",
                    },
                )
            )["scope"]
            grants.append(granted)
            if ordinal == 0:
                denial(
                    await h.call(
                        "beta",
                        "evidence/conflict/read",
                        "b-one-parent",
                        {"conflict_id": conflict["conflict_id"]},
                    ),
                    "RESOURCE_ACCESS_DENIED",
                )
        shared = value(
            await h.call(
                "beta",
                "evidence/conflict/read",
                "b-all-parents",
                {"conflict_id": conflict["conflict_id"]},
            )
        )
        assert "alpha-question-draft-marker" in str(shared)
        assert (
            value(
                await h.call(
                    "beta", "evidence/read", "b-derived-read", {"evidence_id": link["evidence_id"]}
                )
            )["evidence"]["target_id"]
            == "alpha-private-question"
        )
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "b-withdraw-parent",
                {
                    "resource_ref": resources[0],
                    "expected_revision": grants[0]["revision"],
                    "grant_id": grants[0]["grants"][0]["grant_id"],
                    "reason": "withdraw one required source",
                },
            )
        )
        denial(
            await h.call(
                "beta",
                "evidence/conflict/read",
                "b-all-parents",
                {"conflict_id": conflict["conflict_id"]},
            ),
            "RESOURCE_ACCESS_DENIED",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "surface", ["evidence/list", "evidence/packet/read", "evidence/conflict/read"]
)
async def test_mixed_evidence_and_conflict_follow_current_parent_access(
    tmp_path: Path, surface: str
) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            name: ResourceScopeTemplate(
                owner_kind="WORKSTREAM",
                owner_workstream=name,
                visibility="WORKSTREAM",
            )
            for name in ("alpha", "beta")
        }
    )
    async with scope_harness(tmp_path, policy) as h:
        alpha = value(await h.connect("alpha", "alpha", None))["artifact"]["artifact_id"]
        beta = value(await h.connect("beta", "beta", None))["artifact"]["artifact_id"]
        beta_scope = value(
            await h.call("beta", "project/source/scope/read", "beta-scope", {"resource_ref": beta})
        )["scope"]
        shared = value(
            await h.call(
                "beta",
                "project/source/scope/grant",
                "share-beta-alpha",
                {
                    "resource_ref": beta,
                    "expected_revision": beta_scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["alpha"],
                    "reason": "paired evidence review",
                },
            )
        )["scope"]
        spans = value(await h.call("alpha", "evidence/list", "sources-visible", {}))["spans"]
        selected = [
            next(
                item["span_id"]
                for item in spans
                if item["artifact_id"] == ref and "private-marker" in item["exact_text"]
            )
            for ref in (alpha, beta)
        ]
        link = value(
            await h.call(
                "alpha",
                "evidence/link/propose",
                "mixed-link",
                {
                    "target_type": "DECISION_OBJECT",
                    "target_id": "case:paired",
                    "relation": "QUALIFIES",
                    "span_ids": selected,
                    "observed_statement": "mixed-private-marker from both source observations",
                    "independence_group": "review:paired",
                },
            )
        )["evidence"]
        conflict = value(
            await h.call(
                "alpha",
                "evidence/challenge",
                "mixed-conflict",
                {
                    "evidence_ids": [link["evidence_id"]],
                    "field": "timing",
                    "reason": "mixed-private-marker requires a further observation",
                },
            )
        )["conflict"]
        value(
            await h.call(
                "beta",
                "project/source/scope/revoke",
                "withdraw-beta",
                {
                    "resource_ref": beta,
                    "expected_revision": shared["revision"],
                    "grant_id": shared["grants"][0]["grant_id"],
                    "reason": "withdraw this parent source",
                },
            )
        )
        own = value(
            await h.call("alpha", "evidence/read", "own-still-readable", {"span_id": selected[0]})
        )
        assert "alpha-private-marker" in str(own)
        payload: dict[str, object] = {}
        if surface == "evidence/packet/read":
            payload["target_id"] = "case:paired"
        elif surface == "evidence/conflict/read":
            payload["conflict_id"] = conflict["conflict_id"]
        response = await h.call("alpha", surface, "fresh-after-parent-withdrawal", payload)
        if surface == "evidence/conflict/read":
            denial(response, "RESOURCE_ACCESS_DENIED")
        else:
            assert link["evidence_id"] not in str(value(response))
        assert "mixed-private-marker" not in response.text
