from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

import thoth.apps.resource_scope_composition as resource_scope_composition
from thoth.domain.connectors import (
    ConnectorCapability,
    ConnectorOperation,
    ConnectorRouteDecision,
    ConnectorSelectorContract,
    NativeVersionKind,
    SelectorFieldSpec,
)
from thoth.domain.environment import DeploymentKind, EgressPolicy, EnvironmentProfile, ModelRoute
from thoth.ports.connectors import ConnectorPort, ConnectorRegistryPort


class _Registry(ConnectorRegistryPort):
    def __init__(self, items: tuple[ConnectorCapability, ...]) -> None:
        self._items = items

    def capabilities(self) -> tuple[ConnectorCapability, ...]:
        return self._items


    def resolve(self, connector_id: str) -> ConnectorPort:
        raise AssertionError("unexpected connector resolution")

    def route(
        self, selector: Mapping[str, JsonValue], *, requested_connector_id: str | None = None
    ) -> ConnectorRouteDecision:
        raise AssertionError("unexpected connector routing")


def _capability(connector_id: str, source_kind: str, egress_class: str) -> ConnectorCapability:
    return ConnectorCapability(
        connector_id=connector_id,
        source_kind=source_kind,
        egress_class=egress_class,
        driver_version="fixture",
        operations=(ConnectorOperation.READ,),
        native_version_kinds=(NativeVersionKind.NONE,),
        selector_contract=ConnectorSelectorContract(
            fields=(SelectorFieldSpec(name="mode", value_type="STRING"),)
        ),
    )


def _allowlist(
    registry: ConnectorRegistryPort, environment: EnvironmentProfile | None
) -> tuple[str, ...]:
    function: object = vars(resource_scope_composition).get("_default_connector_allowlist")
    assert callable(function)
    result: object = function(registry, environment)
    assert isinstance(result, tuple)
    assert all(isinstance(item, str) for item in cast(tuple[object, ...], result))
    return cast(tuple[str, ...], result)

def test_default_allowlist_excludes_public_rest() -> None:
    registry = _Registry(
        (
            _capability("local-fs", "LOCAL", "NONE"),
            _capability("git", "GIT", "NONE"),
            _capability("public-web", "REST", "PUBLIC"),
            _capability("project-public-web", "REST", "ALLOWLISTED_EXTERNAL"),
            _capability("public-fixture", "REST", "ALLOWLISTED_EXTERNAL"),
        )
    )
    assert _allowlist(registry, None) == ("local-fs", "git")


def test_environment_allowlist_is_used_as_is() -> None:
    registry = _Registry(())
    environment = EnvironmentProfile(
        profile_id="fixture:local",
        deployment=DeploymentKind.LOCAL,
        bind_host="127.0.0.1",
        model_route=ModelRoute.OPENAI_COMPATIBLE_LOCAL,
        egress_policy=EgressPolicy.DENY_ALL,
        connector_allowlist=("public-web",),
        data_residency="LOCAL",
        model_data_may_leave_boundary=False,
        adapter_available=True,
    )
    assert _allowlist(registry, environment) == ("public-web",)
