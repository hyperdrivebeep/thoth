from typing import Protocol

from thoth.domain.web_acquisition import WebPage


class PublicReaderPort(Protocol):
    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage: ...


class AnonymousBrowserPort(Protocol):
    async def render(self, page: WebPage, *, max_bytes: int, timeout: float) -> WebPage: ...
