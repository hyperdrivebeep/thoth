"""Public discovery candidates are read again before they can become evidence."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from thoth.adapters.connectors.common import content_digest, utc_now
from thoth.adapters.parsers.html_parser import HtmlParser
from thoth.domain.artifact import ArtifactEnvelope
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
from thoth.domain.errors import ParserFailure
from thoth.domain.web_acquisition import WebPage, WebTransformation
from thoth.ports.web_reader import AnonymousBrowserPort, PublicReaderPort


class SearchLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        href = values.get("href")
        if tag != "a" or not href or "result__a" not in (values.get("class") or ""):
            return
        target = urljoin("https://html.duckduckgo.com/", href)
        wrapped = parse_qs(urlsplit(target).query).get("uddg")
        self.links.append(wrapped[0] if wrapped else target)


class PublicWebConnector:
    def __init__(
        self,
        reader: PublicReaderPort,
        *,
        connector_id: str,
        browser: AnonymousBrowserPort | None = None,
        search_enabled: bool = False,
    ) -> None:
        self.reader, self.browser, self.search_enabled = reader, browser, search_enabled
        fields = [
            SelectorFieldSpec(name="mode", value_type="STRING"),
            SelectorFieldSpec(name="uri", value_type="STRING", required=False),
        ]
        if search_enabled:
            fields.append(SelectorFieldSpec(name="query", value_type="STRING", required=False))
        self.capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="REST",
            driver_version="public-web-1.0.0",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("ANONYMOUS",),
            native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
            egress_class="ALLOWLISTED_EXTERNAL",
            selector_contract=ConnectorSelectorContract(fields=tuple(fields)),
        )

    async def discover(
        self, request: ConnectorAccessRequest, checkpoint: ConnectorCheckpoint | None = None
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        uri, query = request.selector.get("uri"), request.selector.get("query")
        if (
            request.selector.get("mode") == "READ"
            and isinstance(uri, str)
            and uri
            and query is None
        ):
            urls = (uri,)
        elif (
            request.selector.get("mode") == "SEARCH"
            and isinstance(query, str)
            and 0 < len(query) <= 1000
            and uri is None
            and self.search_enabled
        ):
            page = await self.reader.read(
                "https://html.duckduckgo.com/html/?q=" + quote(query),
                max_bytes=min(request.max_bytes, 512_000),
                timeout=20,
            )
            links = SearchLinks()
            links.feed(page.raw.decode("utf-8", errors="replace"))
            urls = tuple(dict.fromkeys(links.links))[:8]
        else:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED, "ONE_BOUNDED_URI_OR_QUERY_REQUIRED"
            )
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

    @staticmethod
    def parse(page: WebPage, request: ConnectorAccessRequest) -> None:
        if page.media_type not in HtmlParser.media_types:
            return
        HtmlParser().parse(
            ArtifactEnvelope(
                artifact_id="candidate:public-page",
                project_id=request.project_id,
                source_uri=page.final_uri,
                media_type=page.media_type,
                byte_sha256=content_digest(page.raw),
                authority=request.authority,
                cutoff_state=request.cutoff_state,
                security_class=request.security_class,
                retrieved_at=page.retrieved_at,
                parser_name="unparsed",
                parser_version="0",
            ),
            page.raw,
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        if request.selector != {"mode": "READ", "uri": ref.source_uri}:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED, "PUBLIC_FETCH_SELECTOR_MISMATCH"
            )
        original = page = await self.reader.read(
            ref.source_uri, max_bytes=request.max_bytes, timeout=30
        )
        if page.media_type not in {
            "text/html",
            "application/xhtml+xml",
            "text/plain",
            "application/pdf",
        }:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "PUBLIC_MEDIA_UNSUPPORTED")
        transformation = None
        try:
            self.parse(page, request)
        except ParserFailure as exc:
            if "HTML_DYNAMIC_OR_EMPTY" not in str(exc):
                raise ConnectorFailure(ConnectorErrorCode.PARTIAL_FETCH, str(exc)) from exc
            if self.browser is None:
                raise ConnectorFailure(
                    ConnectorErrorCode.PARTIAL_FETCH, "PUBLIC_DYNAMIC_BROWSER_UNAVAILABLE"
                ) from exc
            page = await self.browser.render(page, max_bytes=request.max_bytes, timeout=20)
            self.parse(page, request)
            transformation = WebTransformation(
                requested_uri=ref.source_uri,
                final_uri=page.final_uri,
                http_sha256=content_digest(original.raw),
                http_retrieved_at=original.retrieved_at,
                rendered_sha256=content_digest(page.raw),
                rendered_at=page.retrieved_at,
                renderer="ANONYMOUS_CHROMIUM_BROKERED",
                redirects=page.redirects,
                navigation=page.navigation,
                dom_location_at_capture=page.dom_location_at_capture,
            )
        digest = content_digest(page.raw)
        return ConnectorFetchResult(
            ref=ref.model_copy(
                update={
                    "source_uri": page.final_uri,
                    "locator": {**ref.locator, "uri": page.final_uri},
                    "media_type": page.media_type,
                    "observed_at": page.retrieved_at,
                    "native_version": NativeVersion(
                        kind=NativeVersionKind.CONTENT_HASH, value=digest
                    ),
                }
            ),
            raw=page.raw,
            content_sha256=digest,
            transformation=transformation,
            original_http=original if transformation is not None else None,
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
