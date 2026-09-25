import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.integration import test_public_browser as fixture

from thoth.adapters.connectors.anonymous_browser import AnonymousChromiumReader
from thoth.adapters.connectors.common import utc_now
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.web_acquisition import WebPage

START = "https://fixture.example.org/start"
FINAL = "https://fixture.example.org/final"
BODY = b"<html><body><p>Final measurement 12 ms, alpha condition.</p></body></html>"


class NavigationReader:
    def __init__(self, pages: dict[str, WebPage]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        assert timeout > 0
        self.calls.append(uri)
        if uri not in self.pages:
            raise ConnectorFailure(ConnectorErrorCode.EGRESS_DENIED, "NOT_ALLOWED")
        page = self.pages[uri]
        assert len(page.raw) <= max_bytes
        return page


def page(uri: str, raw: bytes, final: str | None = None) -> WebPage:
    return WebPage(
        requested_uri=uri,
        final_uri=final or uri,
        raw=raw,
        media_type="text/html",
        retrieved_at=utc_now(),
        redirects=(final,) if final else (),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["js", "redirect", "history", "denied"])
async def test_rendered_document_identity_and_lineage(mode: str) -> None:
    target = FINAL if mode != "denied" else "https://denied.example.org/private"
    script = (
        f'location.replace("{target}")'
        if mode != "history"
        else 'history.pushState({},"","/display-only")'
    )
    initial = page(
        START, f'<html><body><div id="root"></div><script>{script}</script></body></html>'.encode()
    )
    reader = NavigationReader({FINAL: page(FINAL, BODY)})
    if mode == "redirect":
        intermediate = "https://fixture.example.org/redirect"
        initial = page(
            START, f'<html><script>location.replace("{intermediate}")</script></html>'.encode()
        )
        reader.pages[intermediate] = page(intermediate, BODY, FINAL)
    browser = AnonymousChromiumReader(reader)
    if mode == "denied":
        with pytest.raises(ConnectorFailure):
            await browser.render(initial, max_bytes=20000, timeout=5)
        return
    rendered = await browser.render(initial, max_bytes=20000, timeout=5)
    assert rendered.final_uri == (START if mode == "history" else FINAL)
    if mode == "history":
        assert rendered.dom_location_at_capture == "https://fixture.example.org/display-only"
        assert rendered.navigation[-1].kind == "HISTORY_LOCATION"
    else:
        assert b"Final measurement" in rendered.raw
        assert any(
            h.to_uri == FINAL and h.document_sha256 == hashlib.sha256(BODY).hexdigest()
            for h in rendered.navigation
        )
        if mode == "redirect":
            assert FINAL in rendered.redirects


@pytest.mark.asyncio
async def test_normal_ingestion_records_final_uri_and_transform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def read(
        self: fixture.FixtureReader, uri: str, *, max_bytes: int, timeout: float
    ) -> WebPage:
        self.calls.append(uri)
        assert timeout > 0 and max_bytes >= len(BODY)
        if uri == fixture.URI:
            return page(
                uri,
                (
                    '<html><body><div id="root"></div><script>'
                    f'location.replace("{FINAL}")</script></body></html>'
                ).encode(),
            )
        assert uri == FINAL
        return page(uri, BODY)

    monkeypatch.setattr(fixture.FixtureReader, "read", read)
    await fixture.test_normal_research_renders_and_retains_http_provenance(tmp_path)
    connection = sqlite3.connect(
        f"file:{(tmp_path / 'db/thoth.sqlite3').as_posix()}?mode=ro", uri=True
    )
    try:
        uris = connection.execute(
            "select source_uri from artifacts where parser_name='html'"
        ).fetchall()
        assert uris == [(FINAL,)]
        payloads = connection.execute(
            "select content_json from control_records where record_type='WEB_TRANSFORMATION'"
        ).fetchall()
        assert payloads
        transformation = json.loads(payloads[0][0])
        assert FINAL in json.dumps(transformation)
        assert "DOCUMENT_NAVIGATION" in json.dumps(transformation)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_page_scripts_cannot_replace_committed_document_identity() -> None:
    raw = (
        BODY
        + (
            "<script>performance.getEntriesByType=()=>[{name:"
            + json.dumps(START)
            + "}];history.replaceState({},'', '/start');</script>"
        ).encode()
    )
    initial = page(START, f'<html><script>location.replace("{FINAL}")</script></html>'.encode())
    reader = NavigationReader({FINAL: page(FINAL, raw)})
    result = await AnonymousChromiumReader(reader).render(initial, max_bytes=20000, timeout=5)
    assert result.final_uri == FINAL
    assert result.dom_location_at_capture == START
    assert result.navigation[-1].kind == "HISTORY_LOCATION"


@pytest.mark.asyncio
async def test_nonnetwork_new_document_is_not_attributed_to_old_https_source() -> None:
    raw = b"<html><script>window.open('about:blank', '_self');</script></html>"
    reader = NavigationReader({})
    with pytest.raises(ConnectorFailure, match="UNVERIFIED_DOCUMENT_CAPTURE"):
        await AnonymousChromiumReader(reader).render(page(START, raw), max_bytes=20000, timeout=5)


@pytest.mark.asyncio
async def test_navigation_during_capture_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.async_api import Page

    original = Page.evaluate
    moved = False

    async def evaluate(self: Page, expression: str, arg: Any = None) -> Any:
        nonlocal moved
        result = await original(self, expression, arg)
        if "outerHTML" in expression and not moved:
            moved = True
            await self.goto(FINAL, wait_until="domcontentloaded")
        return result

    monkeypatch.setattr(Page, "evaluate", evaluate)
    reader = NavigationReader({FINAL: page(FINAL, BODY)})
    with pytest.raises(ConnectorFailure, match="DOCUMENT_CHANGED_DURING_CAPTURE"):
        await AnonymousChromiumReader(reader).render(page(START, BODY), max_bytes=20000, timeout=5)
