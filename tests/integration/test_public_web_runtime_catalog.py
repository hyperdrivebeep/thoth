from pathlib import Path

import pytest
from tests.integration.public_web_test_support import integer, items, record, rpc_value, text
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.storage.workspace_setup import write_setup
from thoth.apps.runtime import create_runtime
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.workspace_setup import WorkspaceSetupState


@pytest.mark.asyncio
async def test_default_runtime_registers_web_but_catalog_stays_local(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path)
    try:
        created = rpc_value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:web-off",
                        "name": "Web off",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        policy = record(created["policy"])
        allowlist = [text(item) for item in items(record(policy["payload"])["connector_allowlist"])]
        assert PROJECT_PUBLIC_WEB_CONNECTOR_ID not in allowlist
        assert record(created["public_web_execution"])["state"] == "OFF"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_saving_arxiv_web_exposes_managed_catalog_path(tmp_path: Path) -> None:
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    runtime = create_runtime(tmp_path)
    try:
        created = rpc_value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:web-on",
                        "name": "Web on",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        updated = rpc_value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "enable",
                    {
                        "project_id": "project:web-on",
                        "expected_revision": integer(created["revision"]),
                        "payload": {
                            "public_web": {
                                "enabled": True,
                                "preferred_hosts": ["arxiv.org"],
                            }
                        },
                    },
                )
            )
        )
        payload = record(record(updated["policy"])["payload"])
        assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in [
            text(item) for item in items(payload["connector_allowlist"])
        ]
        assert "ALLOWLISTED_EXTERNAL" in [
            text(item) for item in items(payload["connector_allowed_egress_classes"])
        ]
        assert record(updated["public_web_execution"])["state"] == "READY"
        read = rpc_value(
            await runtime.bus.dispatch(
                request("project/read", "read", {"project_id": "project:web-on"})
            )
        )
        execution = record(read["public_web_execution"])
        assert execution["state"] == "READY"
        assert "arxiv.org" in [text(item) for item in items(execution["effective_hosts"])]
        assert (
            PROJECT_PUBLIC_WEB_CONNECTOR_ID
            in [text(item) for item in items(execution["managed_connector_ids"])]
        )
    finally:
        runtime.close()
