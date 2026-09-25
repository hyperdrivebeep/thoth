"""`project/policy/update` reconciles web permissions atomically, without I/O."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.connectors.public_reader import PublicHttpsReader, PublicUrlPolicy
from thoth.adapters.storage.workspace_setup import write_setup
from thoth.apps.runtime import create_runtime
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.workspace_setup import WorkspaceSetupState


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    touched: list[str] = []

    def record_resolve(self: PublicUrlPolicy, uri: str):
        touched.append(uri)
        raise AssertionError("policy saves must not resolve public URIs")

    async def record_read(self: PublicHttpsReader, uri: str, **kwargs: object):
        touched.append(uri)
        raise AssertionError("policy saves must not read public URIs")

    monkeypatch.setattr(PublicUrlPolicy, "resolve", record_resolve)
    monkeypatch.setattr(PublicHttpsReader, "read", record_read)
    return touched


@pytest.mark.asyncio
async def test_enable_save_is_atomic_and_makes_no_network_or_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched = _forbid_network(monkeypatch)
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    model = ControlledResearchModel()
    runtime = create_runtime(tmp_path, model_resolver=model)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:rpc",
                        "name": "Policy update RPC",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        updated = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "enable",
                    {
                        "project_id": "project:rpc",
                        "expected_revision": created["revision"],
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
        payload = updated["policy"]["payload"]
        assert updated["revision"] == created["revision"] + 1
        assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in payload["connector_allowlist"]
        assert "ALLOWLISTED_EXTERNAL" in payload["connector_allowed_egress_classes"]
        assert payload["connector_default"] == "DENY"
        grant = payload["public_web"]["workspace_grant_id"]
        assert isinstance(grant, str) and grant.startswith("grant:")
        assert model.calls == []
        assert touched == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_stale_revision_is_rejected_without_changing_policy(
    tmp_path: Path,
) -> None:
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    runtime = create_runtime(tmp_path)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:stale",
                        "name": "Stale revision",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "first",
                    {
                        "project_id": "project:stale",
                        "expected_revision": created["revision"],
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
        stale = await runtime.bus.dispatch(
            request(
                "project/policy/update",
                "stale",
                {
                    "project_id": "project:stale",
                    "expected_revision": created["revision"],
                    "payload": {
                        "public_web": {"enabled": False, "preferred_hosts": []}
                    },
                },
            )
        )
        assert stale.error is not None
        read = value(
            await runtime.bus.dispatch(
                request("project/read", "read", {"project_id": "project:stale"})
            )
        )
        assert read["policy"]["payload"]["public_web"]["enabled"] is True
        assert read["public_web_execution"]["state"] == "READY"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_rejected_save_leaves_previous_policy_and_revision_intact(
    tmp_path: Path,
) -> None:
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    runtime = create_runtime(tmp_path)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:rollback",
                        "name": "Rollback",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        empty_hosts = await runtime.bus.dispatch(
            request(
                "project/policy/update",
                "empty-hosts",
                {
                    "project_id": "project:rollback",
                    "expected_revision": created["revision"],
                    "payload": {"public_web": {"enabled": True, "preferred_hosts": []}},
                },
            )
        )
        assert empty_hosts.error is not None
        assert "PUBLIC_WEB_HOSTS_REQUIRED" in str(empty_hosts.error)
        read = value(
            await runtime.bus.dispatch(
                request("project/read", "read", {"project_id": "project:rollback"})
            )
        )
        assert read["revision"] == created["revision"]
        payload = read["policy"]["payload"]
        assert PROJECT_PUBLIC_WEB_CONNECTOR_ID not in payload["connector_allowlist"]
        assert read["public_web_execution"]["state"] == "OFF"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_mixed_payload_cannot_bypass_reconciliation(tmp_path: Path) -> None:
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    runtime = create_runtime(tmp_path)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:mixed",
                        "name": "Mixed payload",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        mixed = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "mixed",
                    {
                        "project_id": "project:mixed",
                        "expected_revision": created["revision"],
                        "payload": {
                            "external_write": False,
                            "public_web": {
                                "enabled": True,
                                "preferred_hosts": ["arxiv.org"],
                            },
                        },
                    },
                )
            )
        )
        payload = mixed["policy"]["payload"]
        assert PROJECT_PUBLIC_WEB_CONNECTOR_ID in payload["connector_allowlist"]
        assert "ALLOWLISTED_EXTERNAL" in payload["connector_allowed_egress_classes"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_web_can_be_turned_off_after_consent_is_revoked(tmp_path: Path) -> None:
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    runtime = create_runtime(tmp_path)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:revoked",
                        "name": "Revoked consent",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        enabled = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "enable",
                    {
                        "project_id": "project:revoked",
                        "expected_revision": created["revision"],
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
        write_setup(WorkspaceSetupState(internet_consent="DENIED"), tmp_path)
        disabled = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "disable",
                    {
                        "project_id": "project:revoked",
                        "expected_revision": enabled["revision"],
                        "payload": {
                            "public_web": {
                                "enabled": False,
                                "preferred_hosts": ["arxiv.org"],
                            }
                        },
                    },
                )
            )
        )
        payload = disabled["policy"]["payload"]
        assert PROJECT_PUBLIC_WEB_CONNECTOR_ID not in payload["connector_allowlist"]
        assert "ALLOWLISTED_EXTERNAL" not in payload["connector_allowed_egress_classes"]
        assert disabled["public_web_execution"]["state"] == "OFF"
    finally:
        runtime.close()
