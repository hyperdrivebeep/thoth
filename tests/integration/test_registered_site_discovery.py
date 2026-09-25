"""Registered site listing is enumerated, then documents are re-read before evidence."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import JsonValue
from tests.integration.public_web_test_support import (
    LISTING_URI,
    PAPER_URI,
    UNOBSERVED_URI,
    RecordingPublicReader,
    artifact_uris,
    connector_service,
    create_web_project,
    cutoff_at,
    record,
    web_runtime,
)
from tests.integration.storage_coverage_helpers import request, value

from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorFailure,
    ConnectorOperation,
)
from thoth.domain.enums import SecurityClass
from thoth.domain.policy import PolicyExpectation
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID


def _web_request(
    project_id: str, selector: Mapping[str, JsonValue], expectation: PolicyExpectation
) -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:research-discovery",
        project_id=project_id,
        connector_id=PROJECT_PUBLIC_WEB_CONNECTOR_ID,
        operation=ConnectorOperation.DISCOVER,
        selector=dict(selector),
        security_class=SecurityClass.PUBLIC,
        cutoff_at=cutoff_at(),
        max_bytes=2_000_000,
        policy_id=expectation.policy_id,
        policy_revision=expectation.policy_revision,
        policy_digest=expectation.policy_digest,
    )


@pytest.mark.asyncio
async def test_registered_listing_candidates_are_reread_then_bound(tmp_path: Path) -> None:
    reader = RecordingPublicReader()
    runtime = web_runtime(tmp_path, reader)
    try:
        created = await create_web_project(runtime)
        connectors = connector_service(runtime)
        expectation = connectors.current_policy_expectation("p")
        refs = await connectors.discover_candidates(
            _web_request(
                "p",
                {"mode": "SITE_DISCOVER", "entrypoint_id": "arxiv:cs.LG:recent"},
                expectation,
            )
        )
        assert [item.source_uri for item in refs] == [PAPER_URI]
        assert len(refs) == 1
        discovered = next(iter(refs))
        assert isinstance(discovered, ConnectorArtifactRef)
        assert reader.uris == [LISTING_URI]
        listed_before = value(
            await runtime.bus.dispatch(
                request("project/source/list", "before", {"project_id": "p"})
            )
        )
        assert PAPER_URI not in artifact_uris(listed_before)
        assert LISTING_URI not in artifact_uris(listed_before)
        acquired = await connectors.acquire_one(
            _web_request("p", discovered.locator, expectation).model_copy(
                update={"operation": ConnectorOperation.READ}
            )
        )
        assert acquired.source.uri == PAPER_URI
        listed = value(
            await runtime.bus.dispatch(request("project/source/list", "after", {"project_id": "p"}))
        )
        uris = artifact_uris(listed)
        assert PAPER_URI in uris
        assert LISTING_URI not in uris
        assert reader.uris == [LISTING_URI, PAPER_URI]
        assert record(created["public_web_execution"])["state"] == "READY"
        assert reader.uris.count(LISTING_URI) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unobserved_public_read_is_denied_before_io(tmp_path: Path) -> None:
    reader = RecordingPublicReader()
    runtime = web_runtime(tmp_path, reader)
    try:
        await create_web_project(runtime)
        connectors = connector_service(runtime)
        expectation = connectors.current_policy_expectation("p")
        await connectors.discover_candidates(
            _web_request(
                "p",
                {"mode": "SITE_DISCOVER", "entrypoint_id": "arxiv:cs.LG:recent"},
                expectation,
            )
        )
        reader.uris.clear()
        with pytest.raises(ConnectorFailure, match="PUBLIC_READ_BASIS_REQUIRED"):
            await connectors.acquire_one(
                _web_request(
                    "p",
                    {"mode": "READ", "uri": UNOBSERVED_URI},
                    expectation,
                ).model_copy(update={"operation": ConnectorOperation.READ})
            )
        assert reader.uris == []
    finally:
        runtime.close()
