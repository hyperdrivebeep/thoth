"""Opt-in test guards. The xAI fixture is applied only by xAI test modules."""

from collections.abc import Generator
from typing import Any

import httpx
import pytest


@pytest.fixture
def xai_http_guard(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Permit only pure fake transports in xAI tests; block real send seams too."""
    original_sync = httpx.Client.__init__
    original_async = httpx.AsyncClient.__init__

    def allowed(transport: object, *, asynchronous: bool) -> bool:
        return type(transport) is httpx.MockTransport or (
            asynchronous and type(transport) is httpx.ASGITransport
        )

    def sync_client(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        if (
            args
            or not allowed(kwargs.get("transport"), asynchronous=False)
            or kwargs.get("mounts")
            or kwargs.get("proxy") is not None
        ):
            raise AssertionError("XAI_TEST_REAL_HTTP_BLOCKED")
        kwargs["trust_env"] = False
        original_sync(self, *args, **kwargs)

    def async_client(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        if (
            args
            or not allowed(kwargs.get("transport"), asynchronous=True)
            or kwargs.get("mounts")
            or kwargs.get("proxy") is not None
        ):
            raise AssertionError("XAI_TEST_REAL_HTTP_BLOCKED")
        kwargs["trust_env"] = False
        original_async(self, *args, **kwargs)

    def blocked_sync_send(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise AssertionError("XAI_TEST_REAL_HTTP_BLOCKED")

    async def blocked_async_send(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        raise AssertionError("XAI_TEST_REAL_HTTP_BLOCKED")

    monkeypatch.setattr(httpx.Client, "__init__", sync_client)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", async_client)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_sync_send)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async_send)
    yield
