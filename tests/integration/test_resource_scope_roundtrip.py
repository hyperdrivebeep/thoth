"""D02: explicit ownership, scoped reads and deliberate sharing through real HTTP."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import denial, scope_harness, value
from tests.integration.storage_coverage_helpers import domain_snapshot

from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
async def test_missing_resource_owner_is_held_without_canonical_ingestion(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        before = domain_snapshot(h.runtime.ledger.engine)
        response = await h.connect("alpha", "missing-owner", None)
        denial(response, "RESOURCE_SCOPE_REQUIRED")
        assert domain_snapshot(h.runtime.ledger.engine) == before


@pytest.mark.asyncio
async def test_configured_workstream_scope_is_reused_without_defaulting_other_owners(
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
        for ordinal in range(2):
            connected = value(await h.connect("alpha", f"configured-{ordinal}", None))
            scope = value(
                await h.call(
                    "alpha",
                    "project/source/scope/read",
                    f"configured-scope-{ordinal}",
                    {"resource_ref": connected["artifact"]["artifact_id"]},
                )
            )["scope"]
            assert scope["owner_workstream"] == "alpha"
            assert scope["visibility"] == "WORKSTREAM"
        before = domain_snapshot(h.runtime.ledger.engine)
        denial(await h.connect("beta", "unconfigured-beta", None), "RESOURCE_SCOPE_REQUIRED")
        assert domain_snapshot(h.runtime.ledger.engine) == before


@pytest.mark.asyncio
async def test_http_owner_span_scope_and_explicit_grant_roundtrip(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        connected = value(
            await h.connect(
                "alpha",
                "alpha-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )
        artifact = connected["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha", "project/source/scope/read", "owner-scope", {"resource_ref": artifact}
            )
        )["scope"]
        assert scope["owner_workstream"] == "alpha"
        assert scope["visibility"] == "WORKSTREAM"
        own_spans = value(await h.call("alpha", "evidence/list", "alpha-spans", {}))["spans"]
        span = next(
            item
            for item in own_spans
            if item["artifact_id"] == artifact and "alpha-private-marker" in item["exact_text"]
        )
        foreign = value(await h.call("beta", "evidence/list", "beta-spans", {}))
        assert artifact not in str(foreign)
        assert "alpha-private-marker" not in str(foreign)
        denial(
            await h.call("beta", "evidence/read", "beta-denied", {"span_id": span["span_id"]}),
            "RESOURCE_ACCESS_DENIED",
        )
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "share-with-beta",
                {
                    "resource_ref": artifact,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "share this measurement source with the beta reviewer",
                },
            )
        )["scope"]
        assert granted["revision"] == scope["revision"] + 1
        shared = value(
            await h.call("beta", "evidence/read", "beta-shared", {"span_id": span["span_id"]})
        )
        assert "alpha-private-marker" in str(shared)
        assert connected["artifact"]["byte_sha256"] == shared["source"]["sha256"]
