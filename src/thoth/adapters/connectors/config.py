from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import Field, JsonValue, model_validator

from thoth.adapters.connectors.anonymous_browser import AnonymousChromiumReader
from thoth.adapters.connectors.git import GitReadConnector
from thoth.adapters.connectors.http_read import HttpReadConnector
from thoth.adapters.connectors.local import LocalFileConnector
from thoth.adapters.connectors.mcp import McpResourceConnector, OfficialMcpResourceClient
from thoth.adapters.connectors.network_share import NetworkShareReadConnector
from thoth.adapters.connectors.postgres import PostgresReadConnector
from thoth.adapters.connectors.public_reader import PublicHttpsReader, PublicUrlPolicy
from thoth.adapters.connectors.public_web import PublicWebConnector
from thoth.adapters.connectors.registry import ConnectorRegistry
from thoth.adapters.connectors.s3 import S3ReadConnector
from thoth.domain.base import DomainModel
from thoth.ports.connectors import ConnectorPort


class ConnectorDefinition(DomainModel):
    connector_id: str = Field(min_length=1, max_length=160)
    kind: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    root: str | None = Field(default=None, max_length=1_000)
    dsn_env: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    allowed_views: tuple[str, ...] = ()
    bucket: str | None = Field(default=None, max_length=255)
    prefix: str | None = Field(default=None, max_length=1_000)
    endpoint_url: str | None = Field(default=None, max_length=2_000)
    region_name: str | None = Field(default=None, max_length=80)
    server_url: str | None = Field(default=None, max_length=2_000)
    allowed_uri_prefixes: tuple[str, ...] = ()
    base_url: str | None = Field(default=None, max_length=2_000)
    allowed_path_prefixes: tuple[str, ...] = ()
    allowed_content_types: tuple[str, ...] = ()
    max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=512 * 1024 * 1024)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    options: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_public_hosts: tuple[str, ...] = ()
    enable_public_search: bool = False
    allow_anonymous_browser: bool = False

    @model_validator(mode="after")
    def require_kind_fields(self) -> ConnectorDefinition:
        if self.kind == "PUBLIC_WEB":
            if not self.allowed_public_hosts:
                raise ValueError("PUBLIC_WEB requires allowed_public_hosts")
            if self.enable_public_search and "html.duckduckgo.com" not in self.allowed_public_hosts:
                raise ValueError("public search requires its host in allowed_public_hosts")
        if self.kind in {"LOCAL", "GIT"} and self.root is None:
            raise ValueError(f"{self.kind} connector requires root")
        if self.kind == "POSTGRES" and (self.dsn_env is None or not self.allowed_views):
            raise ValueError("POSTGRES connector requires dsn_env and allowed_views")
        if self.kind == "S3" and (self.bucket is None or self.prefix is None):
            raise ValueError("S3 connector requires bucket and prefix")
        if self.kind == "MCP" and (self.server_url is None or not self.allowed_uri_prefixes):
            raise ValueError("MCP connector requires server_url and allowed_uri_prefixes")
        if self.kind == "HTTP_READ" and (
            self.base_url is None
            or not self.allowed_path_prefixes
            or not self.allowed_content_types
        ):
            raise ValueError("HTTP_READ requires base_url, path and content-type allowlists")
        if self.kind == "NETWORK_SHARE" and self.root is None:
            raise ValueError("NETWORK_SHARE connector requires root")
        return self


class ConnectorRegistryConfig(DomainModel):
    connectors: tuple[ConnectorDefinition, ...]

    @model_validator(mode="after")
    def unique_ids(self) -> ConnectorRegistryConfig:
        ids = [item.connector_id for item in self.connectors]
        if len(ids) != len(set(ids)):
            raise ValueError("connector config IDs must be unique")
        return self


ConnectorFactory = Callable[[ConnectorDefinition, Path], ConnectorPort]


class ConnectorFactoryRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ConnectorFactory] = {}

    def register(self, kind: str, factory: ConnectorFactory) -> None:
        normalized = kind.strip().upper()
        if not normalized or normalized in self._factories:
            raise ValueError(f"connector factory already registered or invalid: {kind}")
        self._factories[normalized] = factory

    def create(self, definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
        try:
            factory = self._factories[definition.kind]
        except KeyError as exc:
            raise ValueError(f"connector factory is not registered: {definition.kind}") from exc
        return factory(definition, config_root)


def default_connector_factory_registry() -> ConnectorFactoryRegistry:
    registry = ConnectorFactoryRegistry()
    registry.register("LOCAL", _local_factory)
    registry.register("GIT", _git_factory)
    registry.register("POSTGRES", _postgres_factory)
    registry.register("S3", _s3_factory)
    registry.register("MCP", _mcp_factory)
    registry.register("HTTP_READ", _http_factory)
    registry.register("PUBLIC_WEB", _public_factory)
    registry.register("NETWORK_SHARE", _network_share_factory)
    return registry


def load_connector_registry(
    path: Path,
    *,
    factory_registry: ConnectorFactoryRegistry | None = None,
) -> ConnectorRegistry:
    resolved = path.resolve()
    value = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("connector config must be a YAML object")
    config = ConnectorRegistryConfig.model_validate(value)
    factories = factory_registry or default_connector_factory_registry()
    return ConnectorRegistry(
        tuple(factories.create(definition, resolved.parent) for definition in config.connectors)
    )


def _local_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    return LocalFileConnector(
        _resolve_root(config_root, definition.root),
        connector_id=definition.connector_id,
    )


def _git_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    return GitReadConnector(
        _resolve_root(config_root, definition.root),
        connector_id=definition.connector_id,
    )


def _postgres_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    del config_root
    assert definition.dsn_env is not None
    dsn = os.environ.get(definition.dsn_env)
    if not dsn:
        raise ValueError(f"connector credential environment is unavailable: {definition.dsn_env}")
    return PostgresReadConnector(
        dsn,
        allowed_views=definition.allowed_views,
        connector_id=definition.connector_id,
    )


def _s3_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    del config_root
    assert definition.bucket is not None
    assert definition.prefix is not None
    return S3ReadConnector.from_default_credential_chain(
        allowed_bucket=definition.bucket,
        allowed_prefix=definition.prefix,
        endpoint_url=definition.endpoint_url,
        region_name=definition.region_name,
        connector_id=definition.connector_id,
    )


def _mcp_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    del config_root
    assert definition.server_url is not None
    _validate_mcp_url(definition.server_url)
    return McpResourceConnector(
        OfficialMcpResourceClient(definition.server_url),
        allowed_uri_prefixes=definition.allowed_uri_prefixes,
        connector_id=definition.connector_id,
    )


def _http_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    del config_root
    assert definition.base_url is not None
    return HttpReadConnector(
        base_url=definition.base_url,
        allowed_path_prefixes=definition.allowed_path_prefixes,
        allowed_content_types=definition.allowed_content_types,
        connector_id=definition.connector_id,
        configured_max_bytes=definition.max_bytes,
        timeout_seconds=definition.timeout_seconds,
    )


def _public_factory(definition: ConnectorDefinition, config_root: Path) -> ConnectorPort:
    del config_root
    reader = PublicHttpsReader(
        PublicUrlPolicy(definition.allowed_public_hosts),
        configured_max_bytes=definition.max_bytes,
        configured_timeout_seconds=definition.timeout_seconds,
    )
    return PublicWebConnector(
        reader,
        connector_id=definition.connector_id,
        browser=AnonymousChromiumReader(reader, timeout_seconds=definition.timeout_seconds)
        if definition.allow_anonymous_browser
        else None,
        search_enabled=definition.enable_public_search,
    )


def _network_share_factory(
    definition: ConnectorDefinition,
    config_root: Path,
) -> ConnectorPort:
    return NetworkShareReadConnector(
        _resolve_root(config_root, definition.root),
        connector_id=definition.connector_id,
        configured_max_bytes=definition.max_bytes,
    )


def _resolve_root(config_root: Path, raw: str | None) -> Path:
    if raw is None:
        raise ValueError("connector root is required")
    path = Path(raw)
    return (path if path.is_absolute() else config_root / path).resolve()


def _validate_mcp_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("MCP server_url must use HTTPS or local loopback HTTP")
