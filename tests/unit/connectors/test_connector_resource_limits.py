from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine

from thoth.adapters.connectors.http_read import HttpReadConnector
from thoth.adapters.connectors.mcp import McpResourceConnector
from thoth.adapters.connectors.postgres import PostgresReadConnector
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorOperation,
    NativeVersion,
    NativeVersionKind,
)


def access(
    connector_id: str,
    selector: dict[str, JsonValue],
    *,
    max_bytes: int = 4096,
) -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:security-test",
        project_id="project:security-test",
        connector_id=connector_id,
        selector=selector,
        cutoff_at=datetime(2026, 9, 4, tzinfo=UTC),
        policy_id="policy:security-test",
        policy_revision=1,
        policy_digest="a" * 64,
        max_bytes=max_bytes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative_path",
    (
        "catalog/../secret",
        "catalog/%2e%2e/secret",
        "catalog/%2E%2e%2Fsecret",
        "catalog/%252e%252e%252fsecret",
        "catalog%2f..%2fsecret",
        "catalog\\..\\secret",
        "catalogue/report.md",
    ),
)
async def test_http_connector_rejects_noncanonical_or_sibling_allowlist_paths(
    relative_path: str,
) -> None:
    connector = HttpReadConnector(
        base_url="https://example.invalid/approved/",
        allowed_path_prefixes=("catalog/",),
        allowed_content_types=("text/markdown",),
        connector_id="http-security",
        configured_max_bytes=4096,
        timeout_seconds=2,
    )
    with pytest.raises(ConnectorFailure) as caught:
        await connector.discover(access("http-security", {"relative_path": relative_path}))
    assert caught.value.code == ConnectorErrorCode.SCOPE_DENIED


def test_http_connector_preserves_allowed_nested_path_space_and_query() -> None:
    connector = HttpReadConnector(
        base_url="https://example.invalid/approved/",
        allowed_path_prefixes=("catalog/",),
        allowed_content_types=("text/markdown",),
        connector_id="http-security",
        configured_max_bytes=4096,
        timeout_seconds=2,
    )
    url = connector._url(  # pyright: ignore[reportPrivateUsage]
        access(
            "http-security",
            {"relative_path": "catalog/reports/report%20one.md?version=1"},
        )
    )
    assert url == "https://example.invalid/approved/catalog/reports/report%20one.md?version=1"


class CountingRows:
    def __init__(self) -> None:
        self.yielded = 0

    def mappings(self) -> CountingRows:
        return self

    def all(self) -> list[dict[str, object]]:
        raise AssertionError("PostgreSQL resource limit must not materialize all rows")

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.yielded += 1
        yield {"payload": "x" * 200}
        raise AssertionError("bounded serializer consumed rows after the byte limit")


class FakeTransaction:
    def __init__(self) -> None:
        self.rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True


class FakeConnection:
    def __init__(self, rows: CountingRows, transaction: FakeTransaction) -> None:
        self.rows = rows
        self.transaction = transaction

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return self.transaction

    def execution_options(self, **_options: object) -> FakeConnection:
        return self

    def execute(self, statement: object) -> object:
        return self.rows if str(statement).startswith("SELECT") else object()


class FakeEngine:
    def __init__(self) -> None:
        self.rows = CountingRows()
        self.transaction = FakeTransaction()
        self.disposed = False

    def connect(self) -> FakeConnection:
        return FakeConnection(self.rows, self.transaction)

    def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_postgres_connector_stops_streaming_before_full_result_materialization() -> None:
    engine = FakeEngine()
    connector = PostgresReadConnector(
        "postgresql+psycopg://adapter-owned",
        allowed_views=("approved_metrics",),
        engine_factory=lambda _dsn: cast(Engine, engine),
    )
    request = access(
        "postgres-readonly",
        {"view": "approved_metrics", "limit": 100_000},
        max_bytes=64,
    )
    ref = (await connector.discover(request))[0]

    with pytest.raises(ConnectorFailure) as caught:
        await connector.fetch(request, ref)

    assert caught.value.code == ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED
    assert engine.rows.yielded == 1
    assert engine.transaction.rolled_back is True
    assert engine.disposed is True


class CountingMcp:
    def __init__(self) -> None:
        self.read_count = 0

    async def metadata(self, uri: str) -> dict[str, object]:
        return {"size": 100, "mimeType": "text/plain", "uri": uri}

    async def read(self, uri: str) -> tuple[bytes, str]:
        self.read_count += 1
        return uri.encode(), "text/plain"


@pytest.mark.asyncio
async def test_mcp_metadata_limit_rejects_before_sdk_read() -> None:
    client = CountingMcp()
    connector = McpResourceConnector(
        client,
        allowed_uri_prefixes=("project://approved/",),
    )
    request = access(
        "mcp-resource-readonly",
        {"uri": "project://approved/report"},
        max_bytes=10,
    )
    ref = ConnectorArtifactRef(
        source_uri="project://approved/report",
        locator={"uri": "project://approved/report"},
        media_type="text/plain",
        native_version=NativeVersion(kind=NativeVersionKind.NONE),
        size_hint=100,
        observed_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    with pytest.raises(ConnectorFailure) as caught:
        await connector.fetch(request, ref)
    assert caught.value.code == ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED
    assert client.read_count == 0


def test_mcp_capability_and_prefixes_fail_closed() -> None:
    client = CountingMcp()
    with pytest.raises(ValueError, match="non-empty"):
        McpResourceConnector(client, allowed_uri_prefixes=("",))
    connector = McpResourceConnector(
        client,
        allowed_uri_prefixes=("project://approved",),
    )
    assert ConnectorOperation.SUBSCRIBE not in connector.capability.operations
    with pytest.raises(ConnectorFailure) as caught:
        connector._uri(  # pyright: ignore[reportPrivateUsage]
            access(
                "mcp-resource-readonly",
                {"uri": "project://approved-evil/report"},
            )
        )
    assert caught.value.code == ConnectorErrorCode.SCOPE_DENIED


@pytest.mark.asyncio
async def test_mcp_fetch_rejects_cross_resource_reference_before_read() -> None:
    client = CountingMcp()
    connector = McpResourceConnector(
        client,
        allowed_uri_prefixes=("project://approved/",),
    )
    request = access(
        "mcp-resource-readonly",
        {"uri": "project://approved/b"},
        max_bytes=100,
    )
    ref = ConnectorArtifactRef(
        source_uri="project://approved/a",
        locator={"uri": "project://approved/a"},
        media_type="text/plain",
        native_version=NativeVersion(kind=NativeVersionKind.NONE),
        observed_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    with pytest.raises(ConnectorFailure) as caught:
        await connector.fetch(request, ref)
    assert caught.value.code == ConnectorErrorCode.SCOPE_DENIED
    assert client.read_count == 0
