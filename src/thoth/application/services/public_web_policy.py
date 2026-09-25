from __future__ import annotations

from ipaddress import ip_address
from typing import cast
from urllib.parse import urlsplit

from thoth.domain.public_web_access import (
    MANAGED_WEB_EGRESS_CLASS,
    MANAGED_WEB_OWNERSHIP_KEY,
    MANAGED_WEB_SCHEMA_VERSION,
    PROJECT_PUBLIC_WEB_CONNECTOR_ID,
    ManagedWebPermissionOwnership,
    PublicWebUpdateInput,
)
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def normalize_host(value: str) -> str:
    raw = value.strip().lower()
    if "://" in raw:
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
        raw = parsed.hostname or ""
    if raw.endswith("."):
        raw = raw[:-1]
    if ":" in raw and not raw.startswith("["):
        host, _, port = raw.rpartition(":")
        if port.isdigit():
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "PUBLIC_WEB_HOST_PORT_FORBIDDEN",
            )
        raw = host
    if not raw or "*" in raw or "/" in raw or " " in raw:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_HOST_INVALID")
    try:
        ip_address(raw)
    except ValueError:
        pass
    else:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_HOST_IP_FORBIDDEN")
    try:
        return raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_HOST_INVALID") from exc


def normalize_hosts(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "PUBLIC_WEB_INVALID")
    items = cast(list[object] | tuple[object, ...], values)
    hosts: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_HOST_INVALID")
        host = normalize_host(item)
        if host not in seen:
            seen.add(host)
            hosts.append(host)
    return tuple(hosts)


def normalize_public_web_update(value: object) -> PublicWebUpdateInput:
    if not isinstance(value, dict):
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "PUBLIC_WEB_INVALID")
    payload = cast(dict[object, object], value)
    if "enabled" in payload and not isinstance(payload.get("enabled"), bool):
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "PUBLIC_WEB_INVALID")
    enabled = bool(payload.get("enabled"))
    hosts = normalize_hosts(payload.get("preferred_hosts") or [])
    if enabled and not hosts:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_HOSTS_REQUIRED")
    grant = payload.get("workspace_grant_id")
    if grant is not None and not isinstance(grant, str):
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "PUBLIC_WEB_INVALID")
    return PublicWebUpdateInput(
        enabled=enabled,
        preferred_hosts=hosts,
        workspace_grant_id=grant if isinstance(grant, str) and grant else None,
    )


def _ownership_from_payload(payload: dict[str, object]) -> ManagedWebPermissionOwnership:
    raw = payload.get(MANAGED_WEB_OWNERSHIP_KEY)
    if not isinstance(raw, dict):
        return ManagedWebPermissionOwnership()
    try:
        return ManagedWebPermissionOwnership.model_validate(cast(dict[object, object], raw))
    except (TypeError, ValueError):
        return ManagedWebPermissionOwnership()


def reconcile_managed_web_permissions(
    current: dict[str, object],
    web: PublicWebUpdateInput,
    *,
    connector_registered: bool,
) -> dict[str, object]:
    merged = dict(current)
    ownership = _ownership_from_payload(merged)
    raw_allowlist = merged.get("connector_allowlist", [])
    allowlist_items = (
        cast(list[object] | tuple[object, ...], raw_allowlist)
        if isinstance(raw_allowlist, (list, tuple))
        else ()
    )
    allowlist = [
        str(item)
        for item in allowlist_items
        if isinstance(item, str) and item
    ]
    raw_egress = merged.get("connector_allowed_egress_classes", [])
    egress_items = (
        cast(list[object] | tuple[object, ...], raw_egress)
        if isinstance(raw_egress, (list, tuple))
        else ()
    )
    egress = [
        str(item)
        for item in egress_items
        if isinstance(item, str) and item
    ]
    managed_id = PROJECT_PUBLIC_WEB_CONNECTOR_ID
    if web.enabled:
        if not connector_registered:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_CONNECTOR_UNAVAILABLE"
            )
        added_connectors = list(ownership.connector_ids)
        if managed_id not in allowlist:
            allowlist.append(managed_id)
            if managed_id not in added_connectors:
                added_connectors.append(managed_id)
        added_egress = list(ownership.added_egress_classes)
        if MANAGED_WEB_EGRESS_CLASS not in egress:
            egress.append(MANAGED_WEB_EGRESS_CLASS)
            if MANAGED_WEB_EGRESS_CLASS not in added_egress:
                added_egress.append(MANAGED_WEB_EGRESS_CLASS)
        ownership = ManagedWebPermissionOwnership(
            schema_version=MANAGED_WEB_SCHEMA_VERSION,
            connector_ids=tuple(added_connectors),
            added_egress_classes=tuple(added_egress),
        )
    else:
        managed_connectors = set(ownership.connector_ids)
        allowlist = [item for item in allowlist if item not in managed_connectors]
        added = list(ownership.added_egress_classes)
        if MANAGED_WEB_EGRESS_CLASS in added and MANAGED_WEB_EGRESS_CLASS in egress:
            egress = [item for item in egress if item != MANAGED_WEB_EGRESS_CLASS]
            added = [item for item in added if item != MANAGED_WEB_EGRESS_CLASS]
        ownership = ManagedWebPermissionOwnership(
            schema_version=MANAGED_WEB_SCHEMA_VERSION,
            connector_ids=(),
            added_egress_classes=tuple(added),
        )
    merged["connector_allowlist"] = list(dict.fromkeys(allowlist))
    merged["connector_allowed_egress_classes"] = list(dict.fromkeys(egress))
    merged["public_web"] = {
        "enabled": web.enabled,
        "preferred_hosts": list(web.preferred_hosts),
        "workspace_grant_id": web.workspace_grant_id,
    }
    merged[MANAGED_WEB_OWNERSHIP_KEY] = ownership.model_dump(mode="json")
    return merged
