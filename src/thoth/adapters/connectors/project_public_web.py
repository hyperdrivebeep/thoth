from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from thoth.adapters.connectors.common import utc_now
from thoth.adapters.connectors.public_reader import PublicHttpsReader, PublicUrlPolicy
from thoth.adapters.connectors.public_web import PublicWebConnector
from thoth.adapters.connectors.site_discovery import extract_same_host_candidates
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
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.registered_site_entrypoints import entrypoint_uri, registered_entrypoints
from thoth.ports.web_reader import PublicReaderPort


def _default_reader_factory(hosts: tuple[str, ...]) -> PublicReaderPort:
    return PublicHttpsReader(PublicUrlPolicy(hosts))


def _reject_disguised_search(uri: object) -> None:
    """A managed READ carries a verified public document URI, never a query."""
    if not isinstance(uri, str) or not uri:
        return
    parsed = urlsplit(uri)
    segments = tuple(item.lower() for item in parsed.path.split("/") if item)
    if parsed.query or "search" in segments:
        raise ConnectorFailure(
            ConnectorErrorCode.EGRESS_DENIED, "PUBLIC_QUERY_DISCLOSURE_NOT_APPROVED"
        )


class ProjectPublicWebConnector:
    def __init__(
        self,
        hosts_for: Callable[[str], tuple[str, ...]],
        *,
        connector_id: str = PROJECT_PUBLIC_WEB_CONNECTOR_ID,
        reader_factory: Callable[[tuple[str, ...]], PublicReaderPort] | None = None,
    ) -> None:
        self._hosts_for = hosts_for
        self._reader_factory: Callable[[tuple[str, ...]], PublicReaderPort] = (
            reader_factory or _default_reader_factory
        )
        self._observed_uris: dict[str, set[str]] = {}
        self.capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="REST",
            driver_version="project-public-web-1.0.0",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("ANONYMOUS",),
            native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
            egress_class="ALLOWLISTED_EXTERNAL",
            selector_contract=ConnectorSelectorContract(
                fields=(
                    SelectorFieldSpec(name="mode", value_type="STRING"),
                    SelectorFieldSpec(name="uri", value_type="STRING", required=False),
                    SelectorFieldSpec(name="entrypoint_id", value_type="STRING", required=False),
                ),
                allow_additional_fields=False,
            ),
        )

    def _bound(self, request: ConnectorAccessRequest) -> PublicWebConnector:
        hosts = self._hosts_for(request.project_id)
        if not hosts:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "PUBLIC_WEB_HOSTS_REQUIRED")
        return PublicWebConnector(
            self._reader_factory(hosts),
            connector_id=self.capability.connector_id,
            search_enabled=False,
        )

    def _observe(self, project_id: str, urls: tuple[str, ...]) -> None:
        self._observed_uris.setdefault(project_id, set()).update(urls)

    def _require_observed_read(self, project_id: str, uri: object) -> None:
        if not isinstance(uri, str) or uri not in self._observed_uris.get(project_id, set()):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "PUBLIC_READ_BASIS_REQUIRED")

    async def discover(
        self, request: ConnectorAccessRequest, checkpoint: ConnectorCheckpoint | None = None
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        mode = request.selector.get("mode")
        if mode == "SEARCH" or "query" in request.selector:
            raise ConnectorFailure(
                ConnectorErrorCode.EGRESS_DENIED, "PUBLIC_QUERY_DISCLOSURE_NOT_APPROVED"
            )
        _reject_disguised_search(request.selector.get("uri"))
        hosts = self._hosts_for(request.project_id)
        if mode == "SITE_DISCOVER":
            entrypoint_id = request.selector.get("entrypoint_id")
            if not isinstance(entrypoint_id, str) or not entrypoint_id:
                raise ConnectorFailure(
                    ConnectorErrorCode.SCOPE_DENIED,
                    "SITE_DISCOVERY_UNSUPPORTED",
                )
            try:
                listing = entrypoint_uri(entrypoint_id, hosts)
            except KeyError as exc:
                raise ConnectorFailure(
                    ConnectorErrorCode.SCOPE_DENIED, "SITE_DISCOVERY_UNSUPPORTED"
                ) from exc
            reader = self._reader_factory(hosts)
            page = await reader.read(listing, max_bytes=min(request.max_bytes, 512_000), timeout=20)
            html = page.raw.decode("utf-8", errors="replace")
            urls = extract_same_host_candidates(page.final_uri, html)
            self._observe(request.project_id, urls)
            return tuple(
                ConnectorArtifactRef(
                    source_uri=url,
                    locator={"mode": "READ", "uri": url},
                    media_type="text/html",
                    native_version=NativeVersion(kind=NativeVersionKind.NONE),
                    observed_at=utc_now(),
                )
                for url in urls
            )
        self._require_observed_read(request.project_id, request.selector.get("uri"))
        bound = self._bound(request)
        return await bound.discover(request)

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        _reject_disguised_search(request.selector.get("uri"))
        _reject_disguised_search(ref.source_uri)
        self._require_observed_read(request.project_id, ref.source_uri)
        return await self._bound(request).fetch(request, ref, checkpoint)

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id


def catalog_selectors(hosts: tuple[str, ...]) -> list[dict[str, str]]:
    return [dict(item) for item in registered_entrypoints(hosts)]
