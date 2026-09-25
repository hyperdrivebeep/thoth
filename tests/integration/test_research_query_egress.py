from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_public_browser import FixtureReader, SearchModel
from tests.integration.test_research_request_v2 import setup

from thoth.adapters.connectors.anonymous_browser import AnonymousChromiumReader
from thoth.adapters.connectors.public_web import PublicWebConnector
from thoth.adapters.connectors.registry import ConnectorRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_internal", [False, True])
async def test_automatic_query_uses_explicit_disclosure_ceiling(
    tmp_path: Path, allow_internal: bool
) -> None:
    reader = FixtureReader()
    connector = PublicWebConnector(
        reader, connector_id="public-fixture", browser=AnonymousChromiumReader(reader)
    )
    runtime = await setup(
        tmp_path, SearchModel(), source=False, connector_registry=ConnectorRegistry((connector,))
    )
    try:
        policy = value(
            await runtime.bus.dispatch(
                request("project/policy/read", "policy", {"project_id": "p"})
            )
        )["policy"]
        project = value(
            await runtime.bus.dispatch(request("project/read", "project", {"project_id": "p"}))
        )
        payload = {
            **policy["payload"],
            "connector_allowlist": ["public-fixture"],
            "connector_allowed_egress_classes": ["ALLOWLISTED_EXTERNAL"],
        }
        if allow_internal:
            payload["max_query_egress_security_class"] = "INTERNAL"
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "configure",
                    {
                        "project_id": "p",
                        "expected_revision": project["revision"],
                        "payload": payload,
                    },
                )
            )
        )
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Search for the public fixture record.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        discovery = state["current_result"]["result"]["discovery"]
        if allow_internal:
            assert discovery["acquired_count"] == 1, discovery
            assert reader.calls
        else:
            assert discovery["acquired_count"] == 0 and "EGRESS_DENIED" in discovery["failures"]
            assert reader.calls == []
    finally:
        runtime.close()
