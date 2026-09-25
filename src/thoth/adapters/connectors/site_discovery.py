from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


class SameHostLinks(HTMLParser):
    def __init__(self, page_uri: str, allowed_host: str) -> None:
        super().__init__()
        self.page_uri = page_uri
        self.allowed_host = allowed_host
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        target = urljoin(self.page_uri, href)
        parsed = urlsplit(target)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host != self.allowed_host:
            return
        if parsed.fragment or parsed.username or parsed.password or parsed.query:
            return
        cleaned = parsed._replace(fragment="").geturl()
        if cleaned not in self.links:
            self.links.append(cleaned)


def extract_same_host_candidates(page_uri: str, html: str, *, limit: int = 8) -> tuple[str, ...]:
    host = (urlsplit(page_uri).hostname or "").lower()
    parser = SameHostLinks(page_uri, host)
    parser.feed(html)
    skip = {page_uri, page_uri.rstrip("/")}
    return tuple(link for link in parser.links if link not in skip)[:limit]
