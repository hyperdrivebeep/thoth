"""Fresh browser context; all page network reads are fulfilled by the bounded broker."""

import asyncio
import json

from thoth.adapters.connectors.browser_document_capture import (
    capture_document,
    create_document_identity,
)
from thoth.adapters.connectors.common import content_digest, utc_now
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.web_acquisition import WebNavigationHop, WebPage
from thoth.ports.web_reader import PublicReaderPort


class AnonymousChromiumReader:
    def __init__(self, reader: PublicReaderPort, *, timeout_seconds: float = 20) -> None:
        self.reader = reader
        self.timeout_seconds = timeout_seconds

    async def render(self, page: WebPage, *, max_bytes: int, timeout: float) -> WebPage:
        return await asyncio.wait_for(
            self._render(page, max_bytes=max_bytes, timeout=min(timeout, self.timeout_seconds)),
            min(timeout, self.timeout_seconds) + 2,
        )

    async def _render(self, page: WebPage, *, max_bytes: int, timeout: float) -> WebPage:
        try:
            from playwright.async_api import Route, async_playwright
        except ImportError as exc:
            raise ConnectorFailure(
                ConnectorErrorCode.DRIVER_ERROR, "ANONYMOUS_BROWSER_NOT_INSTALLED"
            ) from exc
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(
                headless=True,
                timeout=timeout * 1000,
                chromium_sandbox=True,
                args=["--disable-background-networking", "--disable-quic", "--no-proxy-server"],
            )
            try:
                context = await browser.new_context(accept_downloads=False, service_workers="block")
                remaining, calls = max_bytes * 2, 0
                failures: list[str] = []
                documents: dict[str, WebPage] = {}
                redirect_cache: dict[str, WebPage] = {}
                pending_documents: set[str] = set()
                navigation = list(page.navigation)
                prior = page.requested_uri
                for uri in page.redirects:
                    navigation.append(
                        WebNavigationHop(
                            from_uri=prior,
                            to_uri=uri,
                            kind="HTTP_REDIRECT",
                            retrieved_at=page.retrieved_at,
                            document_sha256=content_digest(page.raw)
                            if uri == page.final_uri
                            else None,
                        )
                    )
                    prior = uri

                async def route(request: Route) -> None:
                    nonlocal remaining, calls
                    calls += 1
                    if request.request.method != "GET" or calls > 32 or remaining <= 0:
                        failures.append("BROWSER_REQUEST_LIMIT")
                        await request.abort()
                        return
                    if request.request.resource_type not in {
                        "document",
                        "script",
                        "stylesheet",
                        "xhr",
                        "fetch",
                    }:
                        await request.abort()
                        return
                    try:
                        url = request.request.url
                        cached = redirect_cache.pop(url, None)
                        content = cached or (
                            page
                            if url == page.final_uri and calls == 1
                            else await self.reader.read(
                                request.request.url,
                                max_bytes=min(max_bytes, remaining),
                                timeout=timeout,
                            )
                        )
                        if cached is None:
                            remaining -= len(content.raw)
                        if remaining < 0 or len(content.raw) > max_bytes:
                            raise ValueError("BROWSER_RESPONSE_LIMIT")
                        main_document = (
                            request.request.is_navigation_request()
                            and request.request.frame == tab.main_frame
                        )
                        if content.final_uri != url:
                            # The broker followed validated redirects. Make Chromium commit
                            # that URI, rather than fulfilling other-document bytes at url.
                            redirect_cache[content.final_uri] = content
                            if not main_document:
                                raise ValueError("BROWSER_SUBRESOURCE_REDIRECT_UNSUPPORTED")
                            if main_document:
                                prior_uri = url
                                for uri in content.redirects or (content.final_uri,):
                                    navigation.append(
                                        WebNavigationHop(
                                            from_uri=prior_uri,
                                            to_uri=uri,
                                            kind="HTTP_REDIRECT",
                                            retrieved_at=content.retrieved_at,
                                            document_sha256=(
                                                content_digest(content.raw)
                                                if uri == content.final_uri
                                                else None
                                            ),
                                        )
                                    )
                                    prior_uri = uri
                            # A fulfilled HTTP 302 may bypass Playwright routing on
                            # its redirect request. An explicit document navigation
                            # is intercepted again, using the broker-validated cache.
                            shim = (
                                "<html><script>location.replace("
                                + json.dumps(content.final_uri).replace("<", "\\u003c")
                                + ")</script></html>"
                            ).encode("utf-8")
                            remaining -= len(shim)
                            if remaining < 0:
                                raise ValueError("BROWSER_RESPONSE_LIMIT")
                            await request.fulfill(status=200, content_type="text/html", body=shim)
                            return
                        if main_document:
                            documents[url] = content
                            pending_documents.add(url)
                        await request.fulfill(
                            status=200, content_type=content.media_type, body=content.raw
                        )
                    except Exception:
                        failures.append("BROWSER_RESOURCE_READ_FAILED")
                        await request.abort()

                await context.route("**/*", route)
                await context.route_web_socket("**/*", lambda socket: socket.close())
                tab = await context.new_page()
                tab.set_default_timeout(timeout * 1000)

                native_identity = await create_document_identity(
                    context, tab, documents, pending_documents, navigation, page.final_uri
                )

                raw, document_uri, location = await asyncio.wait_for(
                    capture_document(
                        tab,
                        page.final_uri,
                        documents,
                        native_identity,
                        max_bytes,
                        timeout,
                    ),
                    timeout,
                )
                if failures:
                    raise ConnectorFailure(ConnectorErrorCode.PARTIAL_FETCH, failures[0])
                if len(raw) > max_bytes:
                    raise ConnectorFailure(
                        ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED, "BROWSER_DOM_TOO_LARGE"
                    )
                if location != document_uri:
                    navigation.append(
                        WebNavigationHop(
                            from_uri=document_uri,
                            to_uri=location,
                            kind="HISTORY_LOCATION",
                            retrieved_at=utc_now(),
                        )
                    )
                return WebPage(
                    requested_uri=page.requested_uri,
                    final_uri=document_uri,
                    media_type="text/html",
                    raw=raw,
                    retrieved_at=utc_now(),
                    redirects=tuple(h.to_uri for h in navigation if h.kind == "HTTP_REDIRECT"),
                    navigation=tuple(navigation),
                    dom_location_at_capture=location,
                )
            finally:
                await asyncio.wait_for(browser.close(), 2)
