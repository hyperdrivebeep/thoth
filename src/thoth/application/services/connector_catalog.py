"""Pure connector catalog and preferred-host search request projections."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.policy import AuthoritativeExecutionPolicy
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.registered_site_entrypoints import registered_entrypoints
from thoth.domain.workspace_setup import WorkspaceSetupState


def public_web_search_attempts(
    request: ConnectorAccessRequest,
    policy: AuthoritativeExecutionPolicy,
) -> tuple[ConnectorAccessRequest, ...]:
    if request.selector.get("mode") != "SEARCH":
        return (request,)
    if not policy.public_web_enabled or not policy.preferred_hosts:
        return (request,)
    query = request.selector.get("query")
    if not isinstance(query, str) or not query.strip():
        return (request,)
    sites = " OR ".join(f"site:{host}" for host in policy.preferred_hosts)
    scoped_selector = dict(request.selector)
    scoped_selector["query"] = f"({query}) ({sites})"
    scoped = request.model_copy(update={"selector": scoped_selector})
    return (scoped, request)


def connector_web_ready(policy: AuthoritativeExecutionPolicy, setup: WorkspaceSetupState) -> bool:
    grant_ok = (
        setup.internet_consent == "ALLOWED"
        and bool(setup.internet_grant_id)
        and setup.internet_grant_id == policy.workspace_internet_grant_id
    )
    web_ready = (
        policy.public_web_enabled
        and bool(policy.preferred_hosts)
        and grant_ok
        and PROJECT_PUBLIC_WEB_CONNECTOR_ID in policy.connector_allowlist
        and "ALLOWLISTED_EXTERNAL" in policy.connector_allowed_egress_classes
    )
    return web_ready


def project_connector_catalog(
    capabilities: tuple[dict[str, JsonValue], ...],
    policy: AuthoritativeExecutionPolicy,
    web_ready: bool,
) -> tuple[dict[str, JsonValue], ...]:
    return tuple(
        cast(
            dict[str, JsonValue],
            {
                **item,
                "index_freshness": "UNKNOWN",
                "unvisited_scope": "NOT_ENUMERATED",
                "configured_selectors": _catalog_selectors(item["connector_id"], policy),
            },
        )
        for item in capabilities
        if item["connector_id"] in policy.connector_allowlist
        and item["egress_class"] in policy.connector_allowed_egress_classes
        and (item["connector_id"] != PROJECT_PUBLIC_WEB_CONNECTOR_ID or web_ready)
    )


def _catalog_selectors(
    connector_id: JsonValue,
    policy: AuthoritativeExecutionPolicy,
) -> list[JsonValue]:
    if connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID:
        return cast(list[JsonValue], list(registered_entrypoints(policy.preferred_hosts)))
    return [
        cast(JsonValue, route.selector)
        for route in (*policy.acquisition_routes, *policy.counter_search_routes)
        if route.connector_id == connector_id
    ]
