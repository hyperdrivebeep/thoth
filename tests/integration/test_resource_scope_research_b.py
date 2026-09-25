"""B access to a public-created hypothesis follows source grants, not its origin workstream."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import denial, scope_harness, value

from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
async def test_b_hypothesis_access_tracks_source_grant_and_revocation(tmp_path: Path) -> None:
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
        source = value(await h.connect("alpha", "hypothesis-source", None))["artifact"][
            "artifact_id"
        ]
        spans = value(await h.call("alpha", "evidence/list", "hypothesis-spans", {}))["spans"]
        thread = value(
            await h.call(
                "alpha",
                "thread/start",
                "hypothesis-thread",
                {
                    "thread_id": "thread:alpha:research",
                    "problem": "alpha question and private draft",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        hypothesis = value(
            await h.call(
                "alpha",
                "hypothesis/create",
                "private-hypothesis",
                {
                    "object_id": thread["current_object_ids"][0],
                    "portfolio_id": "portfolio:scope-b",
                    "statement": "alpha-question-draft-interpretation",
                    "primary_intent": "PREDICTIVE",
                    "evidence_basis": "SOURCE",
                    "scope": {"workstream": "alpha"},
                    "evidence_refs": [item["span_id"] for item in spans],
                },
            )
        )["hypothesis"]
        investigation = value(
            await h.call(
                "alpha",
                "hypothesis/counterevidence/request",
                "sourced-investigation",
                {
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "source_scope": [],
                },
            )
        )["investigation"]
        investigation_query = {"investigation_id": investigation["investigation_id"]}
        denial(
            await h.call(
                "beta", "investigation/read", "private-investigation", investigation_query
            ),
            "RESOURCE_ACCESS_DENIED",
        )
        target = {"hypothesis_id": hypothesis["hypothesis_id"]}
        denial(
            await h.call("beta", "hypothesis/read", "before-source-grant", target),
            "RESOURCE_ACCESS_DENIED",
        )
        scope = value(
            await h.call(
                "alpha", "project/source/scope/read", "hypothesis-scope", {"resource_ref": source}
            )
        )["scope"]
        shared = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "hypothesis-source-grant",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "B shares derived interpretation with source",
                },
            )
        )["scope"]
        read = value(await h.call("beta", "hypothesis/read", "after-source-grant", target))
        assert read["hypothesis"]["statement"] == hypothesis["statement"]
        assert read["hypothesis"]["scope"]["workstream"] == "alpha"
        shared_investigation = value(
            await h.call(
                "beta",
                "investigation/read",
                "shared-investigation",
                investigation_query,
            )
        )["investigation"]
        assert hypothesis["statement"] in shared_investigation["question"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "withdraw-hypothesis-source",
                {
                    "resource_ref": source,
                    "expected_revision": shared["revision"],
                    "grant_id": shared["grants"][0]["grant_id"],
                    "reason": "withdraw underlying source",
                },
            )
        )
        denial(
            await h.call("beta", "hypothesis/read", "after-source-grant", target),
            "RESOURCE_ACCESS_DENIED",
        )
        denial(
            await h.call(
                "beta", "investigation/read", "revoked-investigation", investigation_query
            ),
            "RESOURCE_ACCESS_DENIED",
        )
