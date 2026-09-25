from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.integration.scoped_runtime import create_runtime

from thoth.adapters.connectors import ConnectorRegistry, S3ReadConnector
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
from thoth.domain.environment import (
    DeploymentKind,
    EgressPolicy,
    EnvironmentProfile,
    ModelRoute,
)
from thoth.protocol.jsonrpc import JsonRpcRequest


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


@pytest.mark.asyncio
async def test_stage_boundary_connect_list_and_read_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text("# Plan\n\nLatency target is below 1 ms.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "create-source-project",
                {
                    "project_id": "project:source",
                    "name": "Source RPC",
                    "cutoff_at": "2026-08-30T10:00:00Z",
                },
            )
        )
        connected = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "connect-plan",
                {
                    "project_id": "project:source",
                    "relative_path": "plan.md",
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        sources = await runtime.bus.dispatch(
            _rpc(
                "project/source/list",
                "list-sources",
                {"project_id": "project:source"},
            )
        )
        evidence = await runtime.bus.dispatch(
            _rpc(
                "evidence/list",
                "list-evidence",
                {"project_id": "project:source"},
            )
        )
        assert evidence.result is not None
        evidence_value = evidence.result["value"]
        assert isinstance(evidence_value, dict)
        spans = evidence_value["evidence"]
        assert isinstance(spans, list)
        first = spans[0]
        assert isinstance(first, dict)
        span_id = first["span_id"]
        assert isinstance(span_id, str)
        read = await runtime.bus.dispatch(
            _rpc(
                "evidence/read",
                "read-evidence",
                {"project_id": "project:source", "span_id": span_id},
            )
        )
    finally:
        runtime.close()

    assert connected.error is None
    assert connected.result is not None
    connected_value = connected.result["value"]
    assert isinstance(connected_value, dict)
    assert connected_value["evidence_count"] == 2
    assert sources.result is not None
    source_value = sources.result["value"]
    assert isinstance(source_value, dict)
    artifacts = source_value["artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) == 1
    assert read.result is not None
    read_value = read.result["value"]
    assert isinstance(read_value, dict)
    read_span = read_value["evidence"]
    assert isinstance(read_span, dict)
    assert read_span["span_id"] == span_id


@pytest.mark.asyncio
async def test_source_connect_rejects_path_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.md").write_text("outside", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        created = await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "escape-project",
                {
                    "project_id": "project:source",
                    "name": "Path boundary",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
        assert created.error is None
        response = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "escape-source",
                {
                    "project_id": "project:source",
                    "relative_path": "../outside.md",
                    "media_type": "text/markdown",
                },
            )
        )
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.data["connector_error"] == "SCOPE_DENIED"


@pytest.mark.asyncio
async def test_environment_allowlist_blocks_unlisted_auto_route(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text("blocked", encoding="utf-8")
    profile = EnvironmentProfile(
        profile_id="profile:test-intranet",
        deployment=DeploymentKind.INTRANET,
        bind_host="0.0.0.0",
        model_route=ModelRoute.OPENAI_COMPATIBLE_LOCAL,
        egress_policy=EgressPolicy.DENY_ALL,
        connector_allowlist=("internal-git-readonly",),
        data_residency="test",
        model_data_may_leave_boundary=False,
        adapter_available=True,
    )
    runtime = create_runtime(workspace, environment_profile=profile)
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "allowlist-project",
                {
                    "project_id": "project:allowlist",
                    "name": "Allowlist",
                    "cutoff_at": "2026-08-31T00:00:00Z",
                },
            )
        )
        response = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "allowlist-source",
                {
                    "project_id": "project:allowlist",
                    "relative_path": "plan.md",
                    "media_type": "text/markdown",
                },
            )
        )
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.data["connector_error"] == "SCOPE_DENIED"


@pytest.mark.asyncio
async def test_connector_selector_rejects_raw_credentials(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text("secret-safe", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "secret-project",
                {
                    "project_id": "project:secret",
                    "name": "Secret boundary",
                    "cutoff_at": "2026-08-31T00:00:00Z",
                },
            )
        )
        response = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "secret-source",
                {
                    "project_id": "project:secret",
                    "relative_path": "plan.md",
                    "selector": {"token": "must-not-enter-selector"},
                    "media_type": "text/markdown",
                },
            )
        )
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.data["connector_error"] == "AUTH_REQUIRED"


class BadDigestConnector:
    capability = ConnectorCapability(
        connector_id="bad-local",
        source_kind="LOCAL",
        driver_version="test",
        operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
        native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
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
        return (
            ConnectorArtifactRef(
                source_uri="bad://digest",
                locator={"relative_path": "bad.txt"},
                media_type="text/plain",
                native_version=NativeVersion(
                    kind=NativeVersionKind.CONTENT_HASH,
                    value="d" * 64,
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
        del request, checkpoint
        return ConnectorFetchResult(
            ref=ref,
            raw=b"actual",
            content_sha256="0" * 64,
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id


@pytest.mark.asyncio
async def test_connector_service_recomputes_untrusted_driver_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = create_runtime(
        workspace,
        connector_registry=ConnectorRegistry((BadDigestConnector(),)),
    )
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "digest-project",
                {
                    "project_id": "project:digest",
                    "name": "Digest boundary",
                    "cutoff_at": "2026-08-31T00:00:00Z",
                },
            )
        )
        response = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "digest-source",
                {
                    "project_id": "project:digest",
                    "connector_id": "bad-local",
                    "selector": {"relative_path": "bad.txt"},
                    "media_type": "text/plain",
                },
            )
        )
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.data["connector_error"] == "SOURCE_MUTATED"


class BombS3:
    def head_object(self, **kwargs: str) -> dict[str, object]:
        raise AssertionError(f"driver I/O happened before egress preflight: {kwargs}")

    def get_object(self, **kwargs: str) -> dict[str, object]:
        raise AssertionError(f"driver I/O happened before egress preflight: {kwargs}")


@pytest.mark.asyncio
async def test_egress_preflight_blocks_driver_io_by_default(tmp_path: Path) -> None:
    connector = S3ReadConnector(
        BombS3(),
        allowed_bucket="approved",
        allowed_prefix="project/",
    )
    runtime = create_runtime(
        tmp_path / "workspace",
        connector_registry=ConnectorRegistry((connector,)),
    )
    try:
        await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "egress-project",
                {
                    "project_id": "project:egress",
                    "name": "Egress boundary",
                    "cutoff_at": "2026-08-31T00:00:00Z",
                },
            )
        )
        response = await runtime.bus.dispatch(
            _rpc(
                "project/source/connect",
                "egress-source",
                {
                    "project_id": "project:egress",
                    "selector": {"bucket": "approved", "key": "project/result.json"},
                    "media_type": "application/json",
                },
            )
        )
    finally:
        runtime.close()

    assert response.error is not None
    assert response.error.data["connector_error"] == "EGRESS_DENIED"
