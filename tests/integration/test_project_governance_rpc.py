from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_full_project_governance_lifecycle_is_rpc_reachable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = create_runtime(workspace)
    project_id = "project:f1-governance"
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "f1-create",
                    {
                        "project_id": project_id,
                        "name": "F1 governance",
                        "description": "full project lifecycle fixture",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        assert created["revision"] == 0
        assert created["lifecycle"] == "DRAFT"

        assigned = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "f1-role-assign",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "actor_id": "human:integrator",
                        "role": "TECHNICAL_INTEGRATOR",
                        "authority_tags": ["R3_TEST_OWNER"],
                    },
                )
            )
        )
        role = assigned["role"]
        assert isinstance(role, dict)
        role_id = str(role["role_assignment_id"])
        assert assigned["revision"] == 1
        listed_roles = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/list",
                    "f1-role-list",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], listed_roles["roles"])) == 1

        policy = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "f1-policy-update",
                    {
                        "project_id": project_id,
                        "expected_revision": 1,
                        "payload": {
                            "connector_default": "DENY",
                            "resource_scope_policy": {
                                "default": {"owner_kind": "PROJECT", "visibility": "PROJECT_SHARED"}
                            },
                            "connector_allowlist": ["local-file-upload"],
                            "connector_allowed_egress_classes": ["NONE"],
                            "max_source_security_class": "RESTRICTED",
                            "sandbox_runtime_allowlist": [],
                            "sandbox_network_policy": "DENY_ALL",
                            "sandbox_allowed_hosts": [],
                            "external_write": False,
                            "physical_action": False,
                        },
                    },
                )
            )
        )
        assert policy["revision"] == 2
        policy_read = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/read",
                    "f1-policy-read",
                    {"project_id": project_id},
                )
            )
        )
        assert isinstance(policy_read["policy"], dict)

        overlay = value(
            await runtime.bus.dispatch(
                request(
                    "project/overlay/update",
                    "f1-overlay",
                    {
                        "project_id": project_id,
                        "expected_revision": 2,
                        "overlay": "KOREA_NATIONAL_RND",
                    },
                )
            )
        )
        assert overlay["revision"] == 3

        imported = value(
            await runtime.bus.dispatch(
                request(
                    "project/reference/import",
                    "f1-reference",
                    {
                        "project_id": project_id,
                        "expected_revision": 3,
                        "origin_project_id": "project:origin",
                        "origin_revision": "revision:origin:1",
                        "rights_status": "REUSE_ALLOWED",
                        "scope": "INTERFACE_SCOPE",
                    },
                )
            )
        )
        assert imported["revision"] == 4
        references = value(
            await runtime.bus.dispatch(
                request(
                    "project/reference/list",
                    "f1-reference-list",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], references["references"])) == 1

        impact = value(
            await runtime.bus.dispatch(
                request(
                    "project/cutoff/impact",
                    "f1-cutoff-impact",
                    {
                        "project_id": project_id,
                        "proposed_cutoff_at": "2026-09-30T00:00:00Z",
                    },
                )
            )
        )["impact"]
        assert isinstance(impact, dict)
        cutoff = value(
            await runtime.bus.dispatch(
                request(
                    "project/cutoff/update",
                    "f1-cutoff-update",
                    {
                        "project_id": project_id,
                        "expected_revision": 4,
                        "cutoff_at": "2026-09-30T00:00:00Z",
                        "expected_impact_digest": impact["impact_digest"],
                    },
                )
            )
        )
        assert cutoff["revision"] == 5

        inbox = workspace / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "plan.md").write_text("# plan\nsource", encoding="utf-8")
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "f1-source-connect",
                    {
                        "project_id": project_id,
                        "relative_path": "plan.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                        "expected_project_revision": 5,
                    },
                )
            )
        )
        binding = connected["binding"]
        assert isinstance(binding, dict)
        binding_id = str(binding["binding_id"])

        activated = value(
            await runtime.bus.dispatch(
                request(
                    "project/activate",
                    "f1-activate",
                    {"project_id": project_id, "expected_revision": 6},
                )
            )
        )
        assert activated["lifecycle"] == "ACTIVE"
        assert activated["revision"] == 7

        disconnected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "f1-source-disconnect",
                    {
                        "project_id": project_id,
                        "binding_id": binding_id,
                        "mode": "REVOKE",
                        "expected_project_revision": 7,
                    },
                )
            )
        )
        disconnected_binding = disconnected["binding"]
        assert isinstance(disconnected_binding, dict)
        assert disconnected_binding["state"] == "REVOKED"

        revoked = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/revoke",
                    "f1-role-revoke",
                    {
                        "project_id": project_id,
                        "expected_revision": 8,
                        "role_assignment_id": role_id,
                    },
                )
            )
        )
        assert revoked["revision"] == 9

        metadata = value(
            await runtime.bus.dispatch(
                request(
                    "project/metadata/update",
                    "f1-metadata",
                    {
                        "project_id": project_id,
                        "expected_revision": 9,
                        "name": "F1 complete governance",
                    },
                )
            )
        )
        assert metadata["revision"] == 10

        stale = await runtime.bus.dispatch(
            request(
                "project/metadata/update",
                "f1-stale",
                {
                    "project_id": project_id,
                    "expected_revision": 0,
                    "description": "stale update",
                },
            )
        )
        assert stale.error is not None
        assert stale.error.code == -32030
    finally:
        runtime.close()
