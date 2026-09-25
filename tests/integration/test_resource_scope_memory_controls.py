"""Public memory candidates inherit the complete current source permissions."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import denial, scope_harness, value

from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
async def test_public_memory_candidate_follows_sources_without_derived_owner(
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
        source = value(await h.connect("alpha", "memory-control-source", None))["artifact"][
            "artifact_id"
        ]
        candidate = value(
            await h.call(
                "alpha",
                "memory/candidate/create",
                "memory-control-create",
                {
                    "producer_role": "REFLECTION",
                    "payload_mode": "MEMORY_ASSERTION",
                    "recall_class": "LESSON",
                    "assertion_candidate": "alpha question and derived interpretation",
                    "scope": {"workstream": "alpha"},
                    "evidence_refs": [source],
                },
            )
        )["candidate"]
        query = {"candidate_id": candidate["record_id"]}
        denial(
            await h.call("beta", "memory/candidate/read", "control-before", query),
            "RESOURCE_ACCESS_DENIED",
        )
        assert (
            value(await h.call("beta", "memory/candidate/list", "control-list-before", {}))[
                "candidates"
            ]
            == []
        )
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "memory-control-scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        shared = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "memory-control-grant",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "share underlying material",
                },
            )
        )["scope"]
        observed = value(await h.call("beta", "memory/candidate/read", "control-after", query))[
            "candidate"
        ]
        assert (
            observed["payload"]["assertion_candidate"]
            == candidate["payload"]["assertion_candidate"]
        )
        assert observed["payload"]["scope"]["workstream"] == "alpha"
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "memory-control-revoke",
                {
                    "resource_ref": source,
                    "expected_revision": shared["revision"],
                    "grant_id": shared["grants"][0]["grant_id"],
                    "reason": "withdraw material",
                },
            )
        )
        denial(
            await h.call("beta", "memory/candidate/read", "control-after", query),
            "RESOURCE_ACCESS_DENIED",
        )


@pytest.mark.asyncio
async def test_admitted_draft_content_shares_with_sources_but_private_drafts_cannot_be_adopted(
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
        base: dict[str, object] = {
            "producer_role": "REFLECTION",
            "payload_mode": "MEMORY_ASSERTION",
            "recall_class": "LESSON",
            "assertion_candidate": "private source-free draft",
            "scope": {"workstream": "alpha"},
            "evidence_refs": [],
        }
        draft = value(await h.call("alpha", "memory/candidate/create", "source-free-draft", base))[
            "candidate"
        ]
        source = value(await h.connect("alpha", "draft-source", None))["artifact"]["artifact_id"]
        child = value(
            await h.call(
                "alpha",
                "memory/candidate/create",
                "sourced-draft",
                {
                    **base,
                    "parent_refs": [draft["record_id"]],
                    "evidence_refs": [source],
                },
            )
        )["candidate"]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "draft-source-scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "draft-source-share",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "share admitted interpretation",
                },
            )
        )
        observed = value(
            await h.call(
                "beta",
                "memory/candidate/read",
                "sourced-draft-read",
                {
                    "candidate_id": child["record_id"],
                },
            )
        )["candidate"]
        assert observed["payload"]["assertion_candidate"] == "private source-free draft"
        value(
            await h.call(
                "beta",
                "memory/candidate/classify",
                "shared-draft-classify",
                {
                    "candidate_id": child["record_id"],
                    "expected_candidate_revision": child["version"],
                    "classifier_profile_ref": "memory:classifier:1",
                },
            )
        )
        denial(
            await h.call(
                "beta",
                "memory/candidate/read",
                "private-draft-read",
                {
                    "candidate_id": draft["record_id"],
                },
            ),
            "RESOURCE_LINEAGE_UNKNOWN",
        )
        denial(
            await h.call(
                "beta",
                "memory/candidate/create",
                "unadmitted-draft-copy",
                {
                    **base,
                    "scope": {"workstream": "beta"},
                    "parent_refs": [draft["record_id"]],
                    "evidence_refs": [source],
                },
            ),
            "RESOURCE_LINEAGE_UNKNOWN",
        )


@pytest.mark.asyncio
async def test_source_bound_memory_can_include_an_admitted_semantic_draft(tmp_path: Path) -> None:
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
        value(
            await h.call(
                "alpha",
                "thread/start",
                "semantic-draft-thread",
                {
                    "thread_id": "thread:alpha:draft",
                    "problem": "unshared original question",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        owner = next(iter(h.runtime.ledger.read_heads(h.project).values()))
        source = value(await h.connect("alpha", "semantic-draft-source", None))["artifact"][
            "artifact_id"
        ]
        request: dict[str, object] = {
            "producer_role": "REFLECTION",
            "payload_mode": "MEMORY_ASSERTION",
            "recall_class": "LESSON",
            "assertion_candidate": "original question incorporated into sourced interpretation",
            "scope": {"workstream": "alpha"},
            "domain_revision_ref": owner,
            "evidence_refs": [source],
        }
        child = value(
            await h.call("alpha", "memory/candidate/create", "semantic-draft-child", request)
        )["candidate"]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "semantic-draft-scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "semantic-draft-grant",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "share admitted question",
                },
            )
        )
        observed = value(
            await h.call(
                "beta",
                "memory/candidate/read",
                "semantic-draft-read",
                {
                    "candidate_id": child["record_id"],
                },
            )
        )["candidate"]
        assert observed["payload"]["assertion_candidate"] == request["assertion_candidate"]
        value(
            await h.call(
                "beta",
                "memory/candidate/classify",
                "semantic-draft-classify",
                {
                    "candidate_id": child["record_id"],
                    "expected_candidate_revision": child["version"],
                    "classifier_profile_ref": "memory:classifier:1",
                },
            )
        )
        denial(
            await h.call(
                "beta",
                "memory/candidate/create",
                "semantic-draft-forgery",
                {
                    **request,
                    "scope": {"workstream": "beta"},
                },
            ),
            "RESOURCE_LINEAGE_UNKNOWN",
        )
