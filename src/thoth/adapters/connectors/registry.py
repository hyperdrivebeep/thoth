from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from thoth.domain.connectors import (
    ConnectorCapability,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorRouteDecision,
)
from thoth.ports.connectors import ConnectorPort


class ConnectorRegistry:
    def __init__(self, connectors: tuple[ConnectorPort, ...] = ()) -> None:
        self._connectors: dict[str, ConnectorPort] = {}
        for connector in connectors:
            self.register(connector)

    def register(self, connector: ConnectorPort) -> None:
        connector_id = connector.capability.connector_id
        if connector_id in self._connectors:
            raise ValueError(f"connector already registered: {connector_id}")
        self._connectors[connector_id] = connector

    def resolve(self, connector_id: str) -> ConnectorPort:
        try:
            return self._connectors[connector_id]
        except KeyError as exc:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                f"connector is not registered: {connector_id}",
            ) from exc

    def capabilities(self) -> tuple[ConnectorCapability, ...]:
        return tuple(self._connectors[key].capability for key in sorted(self._connectors))

    def route(
        self,
        selector: Mapping[str, JsonValue],
        *,
        requested_connector_id: str | None = None,
    ) -> ConnectorRouteDecision:
        candidates = (
            (self.resolve(requested_connector_id),)
            if requested_connector_id is not None
            else tuple(self._connectors[key] for key in sorted(self._connectors))
        )
        matches = [
            connector
            for connector in candidates
            if connector.capability.selector_contract.accepts(selector)
        ]
        if not matches:
            raise ConnectorFailure(
                ConnectorErrorCode.ROUTE_NOT_FOUND,
                "connector route is unavailable for the selector contract",
            )
        highest_priority = max(
            connector.capability.selector_contract.route_priority for connector in matches
        )
        selected = [
            connector
            for connector in matches
            if connector.capability.selector_contract.route_priority == highest_priority
        ]
        if len(selected) != 1:
            raise ConnectorFailure(
                ConnectorErrorCode.ROUTE_AMBIGUOUS,
                "connector route is ambiguous",
            )
        capability = selected[0].capability
        return ConnectorRouteDecision(
            connector_id=capability.connector_id,
            selector_contract_digest=capability.selector_contract.contract_digest,
            matched_fields=tuple(sorted(selector)),
        )
