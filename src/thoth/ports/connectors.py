from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue

from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorCheckpoint,
    ConnectorFetchResult,
    ConnectorRouteDecision,
)


class ConnectorPort(Protocol):
    @property
    def capability(self) -> ConnectorCapability: ...

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]: ...

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult: ...

    async def close(self, connector_run_id: str) -> None: ...


class ConnectorRegistryPort(Protocol):
    def resolve(self, connector_id: str) -> ConnectorPort: ...

    def capabilities(self) -> tuple[ConnectorCapability, ...]: ...

    def route(
        self,
        selector: Mapping[str, JsonValue],
        *,
        requested_connector_id: str | None = None,
    ) -> ConnectorRouteDecision: ...
