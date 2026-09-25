"""Managed public-web URI boundaries hold on every hop, before any connection."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import ClassVar

import pytest

import thoth.adapters.connectors.public_reader as public_reader
from thoth.adapters.connectors.public_reader import PublicHttpsReader, PublicUrlPolicy
from thoth.domain.connectors import ConnectorFailure

_PUBLIC_IP = "93.184.216.34"

type _SocketAddress = tuple[str, int] | tuple[str, int, int, int]
type _AddressInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, _SocketAddress]


def _dns_answers(*addresses: str) -> list[_AddressInfo]:
    answers: list[_AddressInfo] = []
    for address in addresses:
        if ":" in address:
            answers.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)))
        else:
            answers.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)))
    return answers


def _fake_dns(*addresses: str) -> Callable[..., list[_AddressInfo]]:
    def answer(*args: object, **kwargs: object) -> list[_AddressInfo]:
        return _dns_answers(*addresses)

    return answer


_public_dns = _fake_dns(_PUBLIC_IP)


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.org/abs/2609.00001",
        "https://export.arxiv.org/abs/2609.00001",
        "https://rss.arxiv.org/rss/cs.LG",
        "https://user@arxiv.org/abs/2609.00001",
        "https://user:secret@arxiv.org/abs/2609.00001",
        "https://arxiv.org:8443/abs/2609.00001",
        "http://arxiv.org/abs/2609.00001",
        "https://arxiv.org/abs/2609.00001#fragment",
    ],
)
def test_unregistered_or_malformed_uris_never_reach_dns(
    monkeypatch: pytest.MonkeyPatch, uri: str
) -> None:
    def no_dns(*args: object, **kwargs: object) -> None:
        raise AssertionError("a rejected URI must not be resolved")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    with pytest.raises(ConnectorFailure, match="PUBLIC_URI_NOT_ALLOWED"):
        PublicUrlPolicy(("arxiv.org",)).resolve(uri)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "192.168.1.5", "169.254.169.254", "::1", "fd00::1"],
)
def test_private_dns_answers_are_rejected(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_dns(address),
    )
    with pytest.raises(ConnectorFailure, match="PUBLIC_DNS_NONPUBLIC_ADDRESS"):
        PublicUrlPolicy(("arxiv.org",)).resolve("https://arxiv.org/abs/2609.00001")


def test_mixed_public_and_private_dns_answers_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_dns(_PUBLIC_IP, "10.0.0.1"),
    )
    with pytest.raises(ConnectorFailure, match="PUBLIC_DNS_NONPUBLIC_ADDRESS"):
        PublicUrlPolicy(("arxiv.org",)).resolve("https://arxiv.org/abs/2609.00001")


def test_registered_host_matches_exactly_after_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    host, address, target = PublicUrlPolicy(("ArXiv.org",)).resolve(
        "https://ARXIV.org/abs/2609.00001"
    )
    assert host == "arxiv.org"
    assert address == _PUBLIC_IP
    assert target == "/abs/2609.00001"


class _Response:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self._headers = headers

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)


class _RedirectingConnection:
    connected_hosts: ClassVar[list[str]] = []

    def __init__(self, host: str, address: str, timeout: float) -> None:
        _RedirectingConnection.connected_hosts.append(host)

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        del method, target, headers

    def getresponse(self) -> _Response:
        return _Response(302, {"Location": "https://evil.example/exfiltrate"})

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_external_redirect_is_rejected_without_connecting_to_the_next_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RedirectingConnection.connected_hosts = []
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr(public_reader, "PinnedHttpsConnection", _RedirectingConnection)
    reader = PublicHttpsReader(PublicUrlPolicy(("arxiv.org",)))
    with pytest.raises(ConnectorFailure, match="PUBLIC_URI_NOT_ALLOWED"):
        await reader.read("https://arxiv.org/abs/2609.00001", max_bytes=100_000, timeout=5)
    assert _RedirectingConnection.connected_hosts == ["arxiv.org"]
