from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import func, select
from tests.integration.scoped_runtime import create_runtime, fixture_scope_policy

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.schema import (
    artifact_versions,
    artifacts,
    control_records,
    entity_snapshots,
    evidence_sources,
    evidence_spans,
    memory_records,
    plan_executions,
    projects,
    receipts,
    semantic_revisions,
    source_bindings,
    step_execution_attempts,
    structural_nodes,
    working_heads,
)
from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import head_set_digest
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorCheckpoint,
    ConnectorFetchResult,
    ConnectorOperation,
    ConnectorSelectorContract,
    NativeVersion,
    NativeVersionKind,
    SelectorFieldSpec,
)
from thoth.domain.sandbox import SandboxResult, SandboxRunSpec, SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability
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


class BombConnector:
    def __init__(self, *, egress_class: str = "NONE") -> None:
        self.calls: list[str] = []
        self.capability = ConnectorCapability(
            connector_id="bomb-local",
            source_kind="LOCAL",
            driver_version="a11-test",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
            egress_class=egress_class,
            selector_contract=ConnectorSelectorContract(
                fields=(SelectorFieldSpec(name="relative_path", value_type="STRING"),),
            ),
        )

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del request, checkpoint
        self.calls.append("discover")
        return (
            ConnectorArtifactRef(
                source_uri="bomb://must-not-run",
                locator={"relative_path": "blocked.md"},
                media_type="text/markdown",
                native_version=NativeVersion(
                    kind=NativeVersionKind.CONTENT_HASH,
                    value="a" * 64,
                ),
                observed_at=datetime(2026, 8, 31, tzinfo=UTC),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del request, ref, checkpoint
        self.calls.append("fetch")
        raise AssertionError("connector fetch I/O must not run after an A11 denial")

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
        self.calls.append("close")


_STATE_TABLES = (
    projects,
    artifacts,
    artifact_versions,
    structural_nodes,
    evidence_spans,
    source_bindings,
    evidence_sources,
    working_heads,
    entity_snapshots,
    semantic_revisions,
    plan_executions,
    step_execution_attempts,
    memory_records,
    receipts,
    control_records,
)


def state_fingerprint(runtime: AppRuntime, project_id: str) -> tuple[tuple[str, int], ...]:
    with runtime.ledger.engine.connect() as connection:
        values: list[tuple[str, int]] = []
        for table in _STATE_TABLES:
            statement = select(func.count()).select_from(table)
            if "project_id" in table.c:
                statement = statement.where(table.c.project_id == project_id)
            if table is control_records:
                # I/O observations survive failed publication; they are not truth projections.
                statement = statement.where(
                    ~(
                        (table.c.namespace == "CONNECTOR")
                        & (table.c.record_type.in_(("IO", "CLEANUP_USAGE")))
                    )
                )
            values.append((table.name, int(connection.execute(statement).scalar_one())))
        revision = connection.execute(
            select(projects.c.revision).where(projects.c.project_id == project_id)
        ).scalar_one()
        values.append(("project_revision", int(revision)))
    return tuple(values)


def policy_payload(
    *,
    connector_allowlist: list[str],
    allowed_egress_classes: list[str],
    max_security_class: str,
) -> dict[str, object]:
    return {
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": connector_allowlist,
        "connector_allowed_egress_classes": allowed_egress_classes,
        "max_source_security_class": max_security_class,
        "sandbox_runtime_allowlist": ["SCRIPTED"],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "egress_class", "payload", "security_class", "expected_code"),
    (
        (
            "empty-allowlist",
            "NONE",
            policy_payload(
                connector_allowlist=[],
                allowed_egress_classes=["NONE"],
                max_security_class="RESTRICTED",
            ),
            "INTERNAL",
            "POLICY_EMPTY_CONNECTOR_ALLOWLIST",
        ),
        (
            "security-ceiling",
            "NONE",
            policy_payload(
                connector_allowlist=["bomb-local"],
                allowed_egress_classes=["NONE"],
                max_security_class="INTERNAL",
            ),
            "RESTRICTED",
            "SECURITY_CLASS_DENIED",
        ),
        (
            "egress",
            "ALLOWLISTED_EXTERNAL",
            policy_payload(
                connector_allowlist=["bomb-local"],
                allowed_egress_classes=["NONE"],
                max_security_class="RESTRICTED",
            ),
            "INTERNAL",
            "EGRESS_DENIED",
        ),
    ),
)
async def test_source_connect_denies_before_io_and_preserves_truth_state(
    tmp_path: Path,
    scenario: str,
    egress_class: str,
    payload: dict[str, object],
    security_class: str,
    expected_code: str,
) -> None:
    connector = BombConnector(egress_class=egress_class)
    runtime = create_runtime(
        tmp_path / scenario,
        connector_registry=ConnectorRegistry((connector,)),
    )
    project_id = f"project:a11:{scenario}"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    f"{scenario}-create",
                    {
                        "project_id": project_id,
                        "name": f"A11 {scenario}",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        updated = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    f"{scenario}-policy",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "payload": payload,
                    },
                )
            )
        )
        authoritative = cast(dict[str, JsonValue], updated["policy"])
        before = state_fingerprint(runtime, project_id)

        response = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                f"{scenario}-connect",
                {
                    "project_id": project_id,
                    "connector_id": "bomb-local",
                    "selector": {"relative_path": "blocked.md"},
                    "media_type": "text/markdown",
                    "security_class": security_class,
                },
            )
        )
        after = state_fingerprint(runtime, project_id)
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.data["connector_error"] == expected_code
    denial = cast(dict[str, JsonValue], response.error.data["policy_denial"])
    assert denial["denial_basis"] == expected_code
    assert denial["policy_id"] == authoritative["policy_id"]
    assert denial["policy_revision"] == authoritative["version"]
    assert denial["policy_digest"] == authoritative["policy_digest"]
    assert denial["semantic_truth_certified"] is False
    assert connector.calls == []
    assert after == before


@pytest.mark.asyncio
async def test_source_connect_rejects_stale_policy_digest_before_io(tmp_path: Path) -> None:
    connector = BombConnector()
    runtime = create_runtime(
        tmp_path / "stale-policy",
        connector_registry=ConnectorRegistry((connector,)),
    )
    project_id = "project:a11:stale-policy"
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "stale-create",
                    {
                        "project_id": project_id,
                        "name": "A11 stale policy",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        stale = cast(dict[str, JsonValue], created["policy"])
        updated = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "stale-policy-update",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "payload": policy_payload(
                            connector_allowlist=["bomb-local"],
                            allowed_egress_classes=["NONE"],
                            max_security_class="RESTRICTED",
                        ),
                    },
                )
            )
        )
        authoritative = cast(dict[str, JsonValue], updated["policy"])
        before = state_fingerprint(runtime, project_id)

        response = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "stale-connect",
                {
                    "project_id": project_id,
                    "connector_id": "bomb-local",
                    "selector": {"relative_path": "blocked.md"},
                    "media_type": "text/markdown",
                    "expected_policy_id": stale["policy_id"],
                    "expected_policy_revision": stale["version"],
                    "expected_policy_digest": stale["policy_digest"],
                },
            )
        )
        after = state_fingerprint(runtime, project_id)
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.data["connector_error"] == "POLICY_DIGEST_MISMATCH"
    denial = cast(dict[str, JsonValue], response.error.data["policy_denial"])
    assert denial["denial_basis"] == "POLICY_DIGEST_MISMATCH"
    assert denial["policy_id"] == authoritative["policy_id"]
    assert denial["policy_revision"] == authoritative["version"]
    assert denial["policy_digest"] == authoritative["policy_digest"]
    assert denial["semantic_truth_certified"] is False
    assert connector.calls == []
    assert after == before


@pytest.mark.asyncio
async def test_repeated_content_does_not_restore_old_policy_authority(tmp_path: Path) -> None:
    connector = BombConnector()
    runtime = create_runtime(
        tmp_path / "repeat-content", connector_registry=ConnectorRegistry((connector,))
    )
    project_id = "project:a11:repeat-content"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "repeat-create",
                    {
                        "project_id": project_id,
                        "name": "A11 repeated policy content",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        base = policy_payload(
            connector_allowlist=["bomb-local"],
            allowed_egress_classes=["NONE"],
            max_security_class="RESTRICTED",
        )
        policies: list[dict[str, JsonValue]] = []
        for ordinal, label in enumerate(("A", "B", "A")):
            updated = value(
                await runtime.bus.dispatch(
                    request(
                        "project/policy/update",
                        f"repeat-policy-{ordinal}",
                        {
                            "project_id": project_id,
                            "expected_revision": ordinal,
                            "payload": {**base, "label": label},
                        },
                    )
                )
            )
            policies.append(cast(dict[str, JsonValue], updated["policy"]))
        old, _middle, current = policies
        assert old["policy_digest"] == current["policy_digest"]
        assert old["policy_id"] != current["policy_id"]
        assert old["version"] != current["version"]
        before = state_fingerprint(runtime, project_id)
        denied = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "repeat-old-authority",
                {
                    "project_id": project_id,
                    "connector_id": "bomb-local",
                    "selector": {"relative_path": "blocked.md"},
                    "media_type": "text/markdown",
                    "expected_policy_id": old["policy_id"],
                    "expected_policy_revision": old["version"],
                    "expected_policy_digest": old["policy_digest"],
                },
            )
        )
        after = state_fingerprint(runtime, project_id)
    finally:
        runtime.close()
    assert denied.error is not None
    assert denied.error.code == -32030
    assert denied.error.data["connector_error"] == "POLICY_BINDING_MISMATCH"
    denial = cast(dict[str, JsonValue], denied.error.data["policy_denial"])
    assert denial["denial_basis"] == "POLICY_BINDING_MISMATCH"
    assert denial["policy_id"] == current["policy_id"]
    assert denial["policy_revision"] == current["version"]
    assert denial["policy_digest"] == current["policy_digest"]
    assert denial["semantic_truth_certified"] is False
    assert connector.calls == []
    assert after == before


class BombSandbox:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def capability(self) -> SandboxCapability:
        return SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            available=True,
            runtime_version="bomb-test:1",
            security_tier="TEST_ONLY",
            network_modes=("DENY_ALL",),
        )

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        del spec
        self.calls.append("run")
        raise AssertionError("sandbox runtime I/O must not run after an A11 denial")

    async def cancel(self, attempt_id: str) -> bool:
        del attempt_id
        self.calls.append("cancel")
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    (
        ("stale-policy", "POLICY_DIGEST_MISMATCH"),
        ("egress", "EGRESS_DENIED"),
    ),
)
async def test_execution_start_denies_sandbox_policy_before_state_or_runtime_io(
    tmp_path: Path,
    scenario: str,
    expected_code: str,
) -> None:
    sandbox = BombSandbox()
    runtime = create_runtime(tmp_path / f"sandbox-policy-{scenario}", sandbox_adapter=sandbox)
    project_id = f"project:a11:sandbox-policy:{scenario}"
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "sandbox-policy-create",
                    {
                        "project_id": project_id,
                        "name": "A11 sandbox policy",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        authoritative = cast(dict[str, JsonValue], created["policy"])
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "sandbox-policy-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:a11:sandbox-policy",
                        "problem": "Run a bounded policy check",
                        "scope": {"workstream": "a11"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], started["current_object_ids"])[0])
        action_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "sandbox-policy-action",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "primary_purpose": "HYPOTHESIS_DISCRIMINATION",
                        "specification": {
                            "description": "Run evaluator in isolation",
                            "expected_observation_or_change": {"description": "bounded result"},
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["typed terminal result"],
                            "observability": "sandbox receipt",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "runs_untrusted_code": True,
                                "external_write": False,
                            },
                        },
                        "evidence_refs": [],
                    },
                )
            )
        )
        action = cast(dict[str, JsonValue], action_result["action"])
        plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "sandbox-policy-plan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "plan_id": "plan:a11:sandbox-policy",
                        "selected_action_refs": [action["action_id"]],
                        "step_candidates": [
                            {
                                "step_id": "step:a11:sandbox-policy",
                                "action_ref": action["action_id"],
                                "inputs": [],
                                "output_contract": {"type": "sandbox-receipt"},
                                "preconditions": [],
                                "stop_conditions": ["typed terminal result"],
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "runs_untrusted_code": True,
                                    "external_write": False,
                                },
                                "sandbox_spec": {
                                    "runtime_profile": "SCRIPTED",
                                    "image_digest": "scripted:image",
                                    "argv": ["python", "-c", "print(1)"],
                                    "policy_id": authoritative["policy_id"],
                                    "policy_revision": authoritative["version"],
                                    "policy_digest": (
                                        "0" * 64
                                        if scenario == "stale-policy"
                                        else authoritative["policy_digest"]
                                    ),
                                    **(
                                        {
                                            "network_policy": "ALLOWLIST",
                                            "allowed_hosts": ["blocked.example.invalid"],
                                        }
                                        if scenario == "egress"
                                        else {}
                                    ),
                                },
                                "state": "READY",
                            }
                        ],
                        "dependency_edges": [],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], plan_result["plan"])
        before = state_fingerprint(runtime, project_id)

        response = await runtime.bus.dispatch(
            request(
                "execution/start",
                "sandbox-policy-execution",
                {
                    "project_id": project_id,
                    "plan_id": plan["plan_id"],
                    "plan_revision_digest": plan["revision_digest"],
                    "execution_profile_ref": "execution:a11-sandbox-v1",
                    "expected_working_head_digest": head_set_digest(
                        runtime.ledger.read_heads(project_id)
                    ),
                },
            )
        )
        after = state_fingerprint(runtime, project_id)
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.data["sandbox_error"] == expected_code
    denial = cast(dict[str, JsonValue], response.error.data["policy_denial"])
    assert denial["denial_basis"] == expected_code
    assert denial["policy_id"] == authoritative["policy_id"]
    assert denial["policy_revision"] == authoritative["version"]
    assert denial["policy_digest"] == authoritative["policy_digest"]
    assert denial["semantic_truth_certified"] is False
    assert sandbox.calls == []
    assert after == before


class SuccessfulConnector(BombConnector):
    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del request, checkpoint
        self.calls.append("fetch")
        raw = b"# Atomic acquisition\n\nAll rows commit together.\n"
        return ConnectorFetchResult(
            ref=ref,
            raw=raw,
            content_sha256=hashlib.sha256(raw).hexdigest(),
        )


@pytest.mark.asyncio
async def test_acquisition_unit_of_work_rolls_back_every_truth_projection_on_fault(
    tmp_path: Path,
) -> None:
    connector = SuccessfulConnector()

    def fail_after_source_projection(step: str) -> None:
        if step == "after_source_projection":
            raise RuntimeError("injected acquisition commit fault")

    runtime = create_runtime(
        tmp_path / "uow-fault",
        connector_registry=ConnectorRegistry((connector,)),
        acquisition_fault_injector=fail_after_source_projection,
    )
    project_id = "project:a11:uow-fault"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "uow-fault-create",
                    {
                        "project_id": project_id,
                        "name": "A11 UoW fault",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        before = state_fingerprint(runtime, project_id)
        response = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "uow-fault-connect",
                {
                    "project_id": project_id,
                    "connector_id": "bomb-local",
                    "selector": {"relative_path": "atomic.md"},
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        after = state_fingerprint(runtime, project_id)
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.data["connector_error"] == "DRIVER_ERROR"
    assert connector.calls == ["discover", "fetch", "close"]
    assert after == before
    with runtime.ledger.engine.connect() as connection:
        observed = (
            connection.execute(
                select(control_records.c.content_json).where(
                    control_records.c.project_id == project_id,
                    control_records.c.namespace == "CONNECTOR",
                    control_records.c.record_type == "IO",
                )
            )
            .scalars()
            .all()
        )
    assert [json.loads(item)["payload"]["phase"] for item in observed] == ["DISCOVER", "FETCH"]
    with runtime.ledger.engine.connect() as connection:
        cleanup = (
            connection.execute(
                select(control_records.c.content_json)
                .where(
                    control_records.c.project_id == project_id,
                    control_records.c.namespace == "CONNECTOR",
                    control_records.c.record_type == "CLEANUP_USAGE",
                )
                .order_by(control_records.c.version)
            )
            .scalars()
            .all()
        )
    payloads = [json.loads(item)["payload"] for item in cleanup]
    assert [item["state"] for item in payloads] == ["PENDING", "COMPLETED"]
    assert len({item["cleanup_attempt_id"] for item in payloads}) == 1
    assert all(item["parent_operation_id"] and item["connector_run_id"] for item in payloads)
    assert all(item["limit_ms"] == 2000 and item["remote_stop"] == "UNKNOWN" for item in payloads)
    assert payloads[-1]["elapsed_ms"] >= 0
