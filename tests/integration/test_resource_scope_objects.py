"""Problem definitions and their auxiliary views follow current source scope."""

from pathlib import Path

from tests.integration.resource_scope_helpers import denial, scope_harness, value
from tests.integration.storage_coverage_helpers import domain_snapshot


async def test_another_actor_cannot_read_a_source_free_problem_draft(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        started = value(
            await h.call(
                "alpha",
                "thread/start",
                "private-problem",
                {
                    "thread_id": "thread:private-object",
                    "problem": "private object marker",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        object_id = started["current_object_ids"][0]
        own = value(await h.call("alpha", "object/read", "own-object", {"object_id": object_id}))
        assert "private object marker" in str(own)
        peer = await h.call("beta", "object/read", "peer-object", {"object_id": object_id})
        assert "error" in peer.json(), peer.json()
        assert object_id not in str(value(await h.call("beta", "object/list", "peer-list", {})))
        assert "private object marker" not in str(
            value(await h.call("beta", "object/candidate/list", "peer-candidates", {}))
        )
        copied = await h.call(
            "beta",
            "object/materialize",
            "copy-private-thread",
            {
                "thread_id": "thread:private-object",
                "purpose_statement": "copy target question",
                "focus_refs": ["workstream:beta"],
                "trigger_evidence_refs": [],
                "actor_ref": h.actors["beta"],
            },
        )
        denial(copied, "AUTH_DATA_SCOPE_DENIED")


async def test_object_and_auxiliary_views_follow_source_grant_and_revoke(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        value(
            await h.call(
                "alpha",
                "thread/start",
                "object-thread",
                {
                    "thread_id": "thread:object-scope",
                    "problem": "initial private question",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        resource = value(
            await h.connect(
                "alpha",
                "object-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )["artifact"]["artifact_id"]
        spans = value(await h.call("alpha", "evidence/list", "object-spans", {}))["spans"]
        refs = [span["span_id"] for span in spans]
        created = value(
            await h.call(
                "alpha",
                "object/materialize",
                "bound-object",
                {
                    "thread_id": "thread:object-scope",
                    "purpose_statement": "source-bound object marker",
                    "problem_frame": "a distinct source comparison",
                    "focus_refs": ["workstream:alpha"],
                    "trigger_evidence_refs": refs,
                    "profile_refs": ["SYSTEMS_INTEGRATION"],
                    "actor_ref": h.actors["alpha"],
                },
            )
        )
        obj = created["object"]
        assert obj is not None
        related = value(
            await h.call(
                "alpha",
                "object/relation/add",
                "bound-relation",
                {
                    "object_id": obj["object_id"],
                    "expected_revision_digest": obj["revision_digest"],
                    "relation_type": "DEPENDS_ON",
                    "target_ref": "CRITERION:comparison",
                    "semantic_role": "source-bound relation marker",
                    "evidence_refs": refs,
                    "authority_state": "INFORMAL",
                },
            )
        )
        obj = related["object"]
        queries = [
            ("object/read", {"object_id": obj["object_id"]}, "object"),
            (
                "object/candidate/read",
                {"candidate_id": created["candidate"]["candidate_id"]},
                "candidate",
            ),
        ]
        for method, payload, key in queries:
            denial(await h.call("beta", method, "before-" + key, payload), "RESOURCE_ACCESS_DENIED")
        current = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "object-scope",
                {
                    "resource_ref": resource,
                },
            )
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "object-grant",
                {
                    "resource_ref": resource,
                    "expected_revision": current["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "shared research",
                },
            )
        )["scope"]
        for method, payload, key in queries:
            assert "source-bound object marker" in str(
                value(await h.call("beta", method, "shared-" + key, payload))
            )
        shared = value(
            await h.call("beta", "object/read", "shared-relations", {"object_id": obj["object_id"]})
        )
        assert "source-bound relation marker" in str(shared["relations"])
        audit = value(
            await h.call(
                "beta", "object/audit/read", "shared-audit", {"object_id": obj["object_id"]}
            )
        )
        assert audit["records"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "object-revoke",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "review ended",
                },
            )
        )
        for method, payload, key in queries:
            denial(
                await h.call("beta", method, "revoked-" + key, payload), "RESOURCE_ACCESS_DENIED"
            )
        for method in ("object/list", "object/relation/list", "object/audit/read"):
            response = await h.call(
                "beta",
                method,
                "revoked-" + method,
                {"object_id": obj["object_id"]} if method != "object/list" else {},
            )
            assert "source-bound" not in response.text
        before = domain_snapshot(h.runtime.ledger.engine)
        rejected = await h.call(
            "beta",
            "object/frame/revise",
            "revoked-mutation",
            {
                "object_id": obj["object_id"],
                "expected_revision_digest": obj["revision_digest"],
                "frame_patch": {"purpose_statement": "unauthorized change"},
                "evidence_refs": refs,
                "reason": "must be denied",
            },
        )
        denial(rejected, "RESOURCE_ACCESS_DENIED")
        assert domain_snapshot(h.runtime.ledger.engine) == before
