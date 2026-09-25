from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime, fixture_scope_policy
from tests.integration.test_a02_autonomous_acquisition import (
    A02Connector,
    DynamicA02Model,
    StaticModelResolver,
    request,
    value,
)

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.connectors.config import ConnectorDefinition, default_connector_factory_registry
from thoth.adapters.connectors.network_share import NetworkShareReadConnector
from thoth.ports.model import ModelPort


def test_http_and_network_share_factories_are_registered_and_bounded(tmp_path: Path) -> None:
    factories = default_connector_factory_registry()
    http = factories.create(
        ConnectorDefinition(
            connector_id="http-lane",
            kind="HTTP_READ",
            base_url="http://127.0.0.1:8765/approved/",
            allowed_path_prefixes=("catalog/",),
            allowed_content_types=("text/markdown",),
            max_bytes=4096,
            timeout_seconds=2,
        ),
        tmp_path,
    )
    share_root = tmp_path / "share"
    share_root.mkdir()
    share = factories.create(
        ConnectorDefinition(
            connector_id="share-lane",
            kind="NETWORK_SHARE",
            root=str(share_root),
            max_bytes=4096,
        ),
        tmp_path,
    )
    assert http.capability.source_kind == "REST"
    assert http.capability.egress_class == "ALLOWLISTED_EXTERNAL"
    assert share.capability.source_kind == "CUSTOM"
    assert share.capability.egress_class == "INTRANET"


@pytest.mark.asyncio
async def test_network_share_route_blocks_escape_before_read(tmp_path: Path) -> None:
    from thoth.adapters.connectors.network_share import NetworkShareReadConnector
    from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure

    root = tmp_path / "share"
    root.mkdir()
    connector = NetworkShareReadConnector(root, connector_id="share-lane")
    request = ConnectorAccessRequest(
        actor_id="agent:test",
        project_id="project:test",
        connector_id="share-lane",
        selector={"relative_path": "../secret.md"},
        policy_id="policy:test",
        policy_revision=1,
        policy_digest="a" * 64,
        max_bytes=4096,
    )
    with pytest.raises(ConnectorFailure, match="outside"):
        await connector.discover(request)


@pytest.mark.asyncio
async def test_normal_thread_a02_uses_real_network_share_lane(tmp_path: Path) -> None:
    initial = A02Connector()
    share_root = tmp_path / "share"
    share_root.mkdir()
    (share_root / "catalog.md").write_text(
        "# Approved registry\n\ndataset_version: 2026.09\nstatus: approved\n",
        encoding="utf-8",
    )
    share = NetworkShareReadConnector(share_root, connector_id="approved-share")
    runtime = create_runtime(
        tmp_path / "workspace",
        connector_registry=ConnectorRegistry((initial, share)),
        model_resolver=StaticModelResolver(cast(ModelPort, DynamicA02Model())),
    )
    project_id = "project:p3:share"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "p3-project",
                    {
                        "project_id": project_id,
                        "name": "P3 network share lane",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "p3-policy",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "payload": {
                            "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
                            "external_write": False,
                            "physical_action": False,
                            "unknown_action_tier": "R3",
                            "connector_default": "DENY",
                            "connector_allowlist": ["a02-readonly", "approved-share"],
                            "connector_allowed_egress_classes": ["NONE", "INTRANET"],
                            "max_source_security_class": "RESTRICTED",
                            "sandbox_runtime_allowlist": [],
                            "sandbox_network_policy": "DENY_ALL",
                            "sandbox_allowed_hosts": [],
                            "acquisition_routes": [
                                {
                                    "evidence_group": "dataset_version",
                                    "match_terms": ["dataset_version"],
                                    "connector_id": "approved-share",
                                    "selector": {"relative_path": "catalog.md"},
                                    "query_families": ["approved network share registry"],
                                    "max_waves": 1,
                                    "max_results": 1,
                                }
                            ],
                        },
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "p3-initial",
                    {
                        "project_id": project_id,
                        "connector_id": "a02-readonly",
                        "selector": {"relative_path": "initial.md"},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        thread_id = "thread:p3:share"
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "p3-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "Which dataset_version produced the result?",
                        "scope": {"workstream": "connector-lane"},
                    },
                )
            )
        )
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "p3-input",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        acquisition = cast(dict[str, JsonValue], analyzed["autonomous_acquisition"])
        assert acquisition["terminal_state"] == "SUFFICIENT"
        usage = cast(dict[str, JsonValue], acquisition["route_usage"])
        assert usage["connector_id"] == "approved-share"
        assert usage["driver_version"] == "1.0.0"
        capability = cast(dict[str, JsonValue], acquisition["route_capability_snapshot"])
        assert capability["source_kind"] == "CUSTOM"
        assert capability["egress_class"] == "INTRANET"
    finally:
        runtime.close()
