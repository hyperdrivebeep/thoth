"""Anonymous HTTPS with public-address validation and pinned DNS on every redirect."""

import asyncio
import http.client
import ipaddress
import socket
import ssl
from time import monotonic
from urllib.parse import urljoin, urlsplit

from thoth.adapters.connectors.common import utc_now
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.web_acquisition import WebPage


class PublicUrlPolicy:
    def __init__(self, hosts: tuple[str, ...]) -> None:
        if not hosts:
            raise ValueError("public reader requires an explicit host allowlist")
        self.hosts = frozenset(host.lower().encode("idna").decode() for host in hosts)

    def resolve(self, uri: str) -> tuple[str, str, str]:
        parsed = urlsplit(uri)
        host = (parsed.hostname or "").encode("idna").decode().lower()
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or host not in self.hosts
            or parsed.fragment
        ):
            raise ConnectorFailure(ConnectorErrorCode.EGRESS_DENIED, "PUBLIC_URI_NOT_ALLOWED")
        addresses = {
            str(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ConnectorFailure(ConnectorErrorCode.EGRESS_DENIED, "PUBLIC_DNS_NONPUBLIC_ADDRESS")
        target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        return host, sorted(addresses)[0], target


class PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float) -> None:
        self.tls_context = ssl.create_default_context()
        super().__init__(host, timeout=timeout, context=self.tls_context)
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class PublicHttpsReader:
    def __init__(
        self,
        policy: PublicUrlPolicy,
        *,
        configured_max_bytes: int = 2_000_000,
        configured_timeout_seconds: float = 30,
    ) -> None:
        self.policy = policy
        self.max_bytes = configured_max_bytes
        self.timeout_seconds = configured_timeout_seconds

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        timeout = min(timeout, self.timeout_seconds)
        return await asyncio.wait_for(
            asyncio.to_thread(self._read, uri, min(max_bytes, self.max_bytes), timeout), timeout
        )

    def _read(self, uri: str, max_bytes: int, timeout: float) -> WebPage:
        deadline = monotonic() + timeout
        current = uri
        redirects: list[str] = []
        for _ in range(5):
            host, address, target = self.policy.resolve(current)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("PUBLIC_READER_DEADLINE")
            connection = PinnedHttpsConnection(host, address, remaining)
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Host": host,
                        "User-Agent": "THOTH-PublicReader/1.0",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ConnectorFailure(
                            ConnectorErrorCode.SOURCE_NOT_FOUND, "EMPTY_REDIRECT"
                        )
                    current = urljoin(current, location)
                    redirects.append(current)
                    continue
                if response.status != 200:
                    raise ConnectorFailure(
                        ConnectorErrorCode.AUTH_REQUIRED
                        if response.status in {401, 403}
                        else ConnectorErrorCode.SOURCE_NOT_FOUND,
                        "PUBLIC_HTTP_STATUS_REJECTED",
                    )
                media = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
                if response.getheader("Content-Encoding") not in {None, "identity"}:
                    raise ConnectorFailure(
                        ConnectorErrorCode.SCOPE_DENIED, "PUBLIC_ENCODING_UNSUPPORTED"
                    )
                chunks: list[bytes] = []
                received = 0
                while True:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("PUBLIC_READER_DEADLINE")
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                    chunk = response.read1(min(65536, max_bytes + 1 - received))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise ConnectorFailure(
                            ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED, "PUBLIC_RESPONSE_TOO_LARGE"
                        )
                    chunks.append(chunk)
                return WebPage(
                    requested_uri=uri,
                    final_uri=current,
                    media_type=media,
                    raw=b"".join(chunks),
                    retrieved_at=utc_now(),
                    redirects=tuple(redirects),
                )
            finally:
                connection.close()
        raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "PUBLIC_REDIRECT_LIMIT")
