"""Capture a DOM together with its broker-validated document identity."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from thoth.adapters.connectors.common import content_digest
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.web_acquisition import WebNavigationHop, WebPage

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


async def capture_document(
    tab: Page,
    initial_uri: str,
    documents: dict[str, WebPage],
    identity: Callable[[], Awaitable[tuple[str, str]]],
    max_bytes: int,
    timeout: float,
) -> tuple[bytes, str, str]:
    await tab.goto(initial_uri, wait_until="domcontentloaded")
    with suppress(Exception):
        await tab.wait_for_load_state("networkidle", timeout=min(timeout, 5) * 1000)
    before, location = await identity(), tab.url
    captured = cast(
        dict[str, object],
        await tab.evaluate(
            "(limit) => { const text = document.documentElement.outerHTML; "
            "if (new TextEncoder().encode(text).length > limit) "
            "throw new Error('DOM_LIMIT'); "
            "return {text, location: document.URL}; }",
            max_bytes,
        ),
    )
    await tab.evaluate("() => document.readyState")
    if await identity() != before or tab.url != location or captured.get("location") != location:
        raise ConnectorFailure(ConnectorErrorCode.PARTIAL_FETCH, "DOCUMENT_CHANGED_DURING_CAPTURE")
    text = captured.get("text")
    document_uri = before[0]
    if document_uri not in documents:
        raise ConnectorFailure(ConnectorErrorCode.PARTIAL_FETCH, "UNVERIFIED_DOCUMENT_CAPTURE")
    if not isinstance(text, str):
        raise ConnectorFailure(ConnectorErrorCode.PARTIAL_FETCH, "BROWSER_DOM_INVALID")
    return text.encode("utf-8"), document_uri, location


async def create_document_identity(
    context: BrowserContext,
    tab: Page,
    documents: dict[str, WebPage],
    pending_documents: set[str],
    navigation: list[WebNavigationHop],
    initial_uri: str,
) -> Callable[[], Awaitable[tuple[str, str]]]:
    verified_loaders: dict[str, str] = {}
    previous_document = initial_uri
    session = await context.new_cdp_session(tab)
    send = cast(Callable[[str], Awaitable[dict[str, object]]], session.send)

    def committed(event: dict[str, object]) -> None:
        nonlocal previous_document
        frame = cast(dict[str, object], event.get("frame", {}))
        if frame.get("parentId"):
            return
        uri, loader = str(frame.get("url", "")), str(frame.get("loaderId", ""))
        if not uri or not loader:
            return
        if uri in pending_documents:
            pending_documents.remove(uri)
            verified_loaders[loader] = uri
        committed_uri = verified_loaders.get(loader)
        if committed_uri is None:
            return
        if previous_document != committed_uri:
            content = documents[committed_uri]
            navigation.append(
                WebNavigationHop(
                    from_uri=previous_document,
                    to_uri=committed_uri,
                    kind="DOCUMENT_NAVIGATION",
                    retrieved_at=content.retrieved_at,
                    document_sha256=content_digest(content.raw),
                )
            )
        previous_document = committed_uri

    session.on("Page.frameNavigated", committed)
    await send("Page.enable")

    async def native_identity() -> tuple[str, str]:
        tree = await send("Page.getFrameTree")
        frame = cast(dict[str, object], cast(dict[str, object], tree["frameTree"])["frame"])
        loader = str(frame.get("loaderId", ""))
        return verified_loaders.get(loader, ""), loader

    return native_identity
