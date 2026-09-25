from __future__ import annotations

from typing import Literal, cast

from thoth.adapters.connectors.registry import ConnectorRegistry
from thoth.domain.connectors import (
    ConnectorCapability,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorOperation,
    ConnectorSelectorContract,
    NativeVersionKind,
    SelectorFieldSpec,
)
from thoth.ports.connectors import ConnectorPort


class ContractOnlyConnector:
    def __init__(
        self,
        connector_id: str,
        *,
        source_kind: str,
        fields: tuple[SelectorFieldSpec, ...],
        priority: int = 0,
    ) -> None:
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind=source_kind,
            driver_version="test:1",
            operations=(ConnectorOperation.READ,),
            native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
            selector_contract=ConnectorSelectorContract(
                fields=fields,
                route_priority=priority,
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability


def field(
    name: str,
    value_type: Literal["STRING", "INTEGER", "BOOLEAN"] = "STRING",
) -> SelectorFieldSpec:
    return SelectorFieldSpec(name=name, value_type=value_type, required=True)


def test_registry_routes_new_rest_and_custom_contracts_without_core_changes() -> None:
    rest = ContractOnlyConnector(
        "rest-extension",
        source_kind="REST",
        fields=(field("endpoint"), field("resource_id")),
    )
    custom = ContractOnlyConnector(
        "custom-extension",
        source_kind="CUSTOM",
        fields=(field("artifact_uri"),),
    )
    registry = ConnectorRegistry((cast(ConnectorPort, rest), cast(ConnectorPort, custom)))

    assert registry.route({"endpoint": "/v1", "resource_id": "42"}).connector_id == (
        "rest-extension"
    )
    assert registry.route({"artifact_uri": "urn:test:42"}).connector_id == (
        "custom-extension"
    )


def test_registry_route_fails_closed_when_selector_is_ambiguous() -> None:
    first = ContractOnlyConnector(
        "first",
        source_kind="CUSTOM",
        fields=(field("artifact_uri"),),
    )
    second = ContractOnlyConnector(
        "second",
        source_kind="CUSTOM",
        fields=(field("artifact_uri"),),
    )
    registry = ConnectorRegistry((cast(ConnectorPort, first), cast(ConnectorPort, second)))

    try:
        registry.route({"artifact_uri": "urn:test:42"})
    except ConnectorFailure as exc:
        assert exc.code is ConnectorErrorCode.ROUTE_AMBIGUOUS
    else:
        raise AssertionError("ambiguous connector route must fail closed")


def test_explicit_connector_must_accept_the_selector_contract() -> None:
    registry = ConnectorRegistry(
        (
            cast(
                ConnectorPort,
                ContractOnlyConnector(
                    "rest-extension",
                    source_kind="REST",
                    fields=(field("endpoint"), field("resource_id")),
                ),
            ),
        )
    )

    try:
        registry.route({"artifact_uri": "urn:test:42"}, requested_connector_id="rest-extension")
    except ConnectorFailure as exc:
        assert exc.code is ConnectorErrorCode.ROUTE_NOT_FOUND
    else:
        raise AssertionError("explicit connector with incompatible selector must fail closed")
