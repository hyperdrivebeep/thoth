from __future__ import annotations

import base64
from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self, cast

from thoth.adapters.connectors.common import content_digest, selector_text, utc_now
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorCheckpoint,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorFetchResult,
    ConnectorOperation,
    ConnectorSelectorContract,
    NativeVersion,
    NativeVersionKind,
    SelectorFieldSpec,
)


class McpResourceClientPort(Protocol):
    async def metadata(self, uri: str) -> dict[str, object]: ...
    async def read(self, uri: str) -> tuple[bytes, str]: ...


class _McpAnnotations(Protocol):
    last_modified: str | None


class _McpResource(Protocol):
    uri: str
    mime_type: str | None
    size: int | None
    annotations: _McpAnnotations | None


class _McpListResult(Protocol):
    resources: list[_McpResource]
    next_cursor: str | None


class _McpContent(Protocol):
    mime_type: str | None

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class _McpReadResult(Protocol):
    contents: list[_McpContent]


class _McpClientContext(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    async def list_resources(self, *, cursor: str | None = None) -> _McpListResult: ...

    async def read_resource(self, uri: str) -> _McpReadResult: ...


class OfficialMcpResourceClient:
    """Small bridge over the official MCP v2 Client without persisting credentials."""

    def __init__(
        self,
        server: str,
        *,
        client_factory: Callable[[str], _McpClientContext] | None = None,
    ) -> None:
        self._server = server
        self._client_factory = client_factory

    def _client(self) -> _McpClientContext:
        if self._client_factory is not None:
            return self._client_factory(self._server)
        from mcp import Client

        return cast(_McpClientContext, Client(self._server))

    async def metadata(self, uri: str) -> dict[str, object]:
        client = self._client()
        async with client as session:
            cursor: str | None = None
            while True:
                result = await session.list_resources(cursor=cursor)
                for resource in result.resources:
                    if str(resource.uri) != uri:
                        continue
                    annotations = resource.annotations
                    return {
                        "mimeType": resource.mime_type,
                        "size": resource.size,
                        "lastModified": (
                            None if annotations is None else annotations.last_modified
                        ),
                    }
                cursor = result.next_cursor
                if cursor is None:
                    break
        raise FileNotFoundError(uri)

    async def read(self, uri: str) -> tuple[bytes, str]:
        client = self._client()
        async with client as session:
            result = await session.read_resource(uri)
        if not result.contents:
            raise ValueError("MCP resource returned no content")
        chunks: list[bytes] = []
        media_type = "application/octet-stream"
        for content in result.contents:
            media_type = content.mime_type or media_type
            payload = content.model_dump(mode="python")
            if isinstance(payload.get("text"), str):
                chunks.append(str(payload["text"]).encode())
            else:
                blob = payload.get("blob")
                if not isinstance(blob, str):
                    raise ValueError("MCP resource content has no text or blob")
                chunks.append(base64.b64decode(blob, validate=True))
        return b"".join(chunks), media_type


class McpResourceConnector:
    def __init__(
        self,
        client: McpResourceClientPort,
        *,
        allowed_uri_prefixes: tuple[str, ...],
        connector_id: str = "mcp-resource-readonly",
    ) -> None:
        self._client = client
        if not allowed_uri_prefixes or any(not value.strip("/") for value in allowed_uri_prefixes):
            raise ValueError("MCP URI prefixes must be non-empty")
        self._prefixes = tuple(value.rstrip("/") for value in allowed_uri_prefixes)
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="MCP",
            driver_version="mcp-python-sdk-compatible:2",
            operations=(
                ConnectorOperation.DISCOVER,
                ConnectorOperation.READ,
            ),
            auth_modes=("OAUTH_PKCE", "STDIO_ENV"),
            native_version_kinds=(NativeVersionKind.ETAG, NativeVersionKind.CONTENT_HASH),
            checkpoint_kind="MCP_CURSOR",
            egress_class="ALLOWLISTED_EXTERNAL",
            selector_contract=ConnectorSelectorContract(
                fields=(SelectorFieldSpec(name="uri", value_type="STRING"),),
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability

    def _uri(self, request: ConnectorAccessRequest) -> str:
        uri = selector_text(request.selector, "uri")
        if not any(uri == prefix or uri.startswith(f"{prefix}/") for prefix in self._prefixes):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "MCP URI outside scope")
        return uri

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        uri = self._uri(request)
        try:
            metadata = await self._client.metadata(uri)
        except Exception as exc:
            raise ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "MCP metadata failed") from exc
        version = str(metadata.get("etag") or metadata.get("lastModified") or "")
        size_value = metadata.get("size")
        size_hint = size_value if isinstance(size_value, int) else None
        return (
            ConnectorArtifactRef(
                source_uri=uri,
                locator={"uri": uri},
                media_type=str(metadata.get("mimeType", "application/octet-stream")),
                native_version=(
                    NativeVersion(kind=NativeVersionKind.ETAG, value=version)
                    if version
                    else NativeVersion(kind=NativeVersionKind.NONE)
                ),
                size_hint=size_hint,
                observed_at=utc_now(),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        uri = self._uri(request)
        if ref.source_uri != uri or ref.locator.get("uri") != uri:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "MCP artifact reference does not match the requested URI",
            )
        if ref.size_hint is not None and ref.size_hint > request.max_bytes:
            raise ConnectorFailure(
                ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED,
                "MCP resource metadata exceeds the content limit",
            )
        try:
            raw, media_type = await self._client.read(uri)
        except Exception as exc:
            raise ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "MCP read failed") from exc
        if len(raw) > request.max_bytes:
            raise ConnectorFailure(
                ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED, "MCP resource too large"
            )
        effective_ref = ref.model_copy(update={"media_type": media_type})
        return ConnectorFetchResult(
            ref=effective_ref,
            raw=raw,
            content_sha256=content_digest(raw),
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
