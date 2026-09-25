from __future__ import annotations

import asyncio
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from thoth.adapters.connectors.common import content_digest, utc_now
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


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class HttpReadConnector:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_path_prefixes: tuple[str, ...],
        allowed_content_types: tuple[str, ...],
        connector_id: str,
        configured_max_bytes: int,
        timeout_seconds: int,
    ) -> None:
        parsed = urlparse(base_url)
        if not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("HTTP connector requires HTTPS or loopback HTTP")
        if not allowed_path_prefixes or not allowed_content_types:
            raise ValueError("HTTP connector requires path and content-type allowlists")
        self._base_url = base_url.rstrip("/") + "/"
        self._base = urlparse(self._base_url)
        self._paths = tuple(self._canonical_prefix(value) for value in allowed_path_prefixes)
        self._types = frozenset(allowed_content_types)
        self._max_bytes = configured_max_bytes
        self._timeout = timeout_seconds
        self._opener = build_opener(_NoRedirect())
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="REST",
            driver_version="1.0.0",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("ADAPTER_OWNED",),
            native_version_kinds=(NativeVersionKind.ETAG, NativeVersionKind.SNAPSHOT),
            checkpoint_kind="ETAG_OR_LAST_MODIFIED",
            egress_class="ALLOWLISTED_EXTERNAL",
            selector_contract=ConnectorSelectorContract(
                fields=(SelectorFieldSpec(name="relative_path", value_type="STRING"),),
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability

    def _url(self, request: ConnectorAccessRequest) -> str:
        raw = request.selector.get("relative_path")
        if not isinstance(raw, str) or not raw:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "HTTP selector requires an allowed relative_path",
            )
        candidate, query = self._canonical_path(raw)
        if not any(
            candidate == prefix or candidate.startswith(f"{prefix}/") for prefix in self._paths
        ):
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "HTTP path is outside approved prefixes",
            )
        encoded = "/".join(quote(segment, safe="-._~") for segment in candidate.split("/"))
        url = urljoin(self._base_url, encoded + (f"?{query}" if query else ""))
        parsed = urlparse(url)
        if (
            parsed.scheme != self._base.scheme
            or parsed.hostname != self._base.hostname
            or parsed.port != self._base.port
            or not parsed.path.startswith(self._base.path)
        ):
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "HTTP route escaped configured origin",
            )
        return url

    @staticmethod
    def _canonical_prefix(value: str) -> str:
        candidate, query = HttpReadConnector._canonical_path(value)
        if query:
            raise ValueError("HTTP allowed path prefixes cannot contain a query")
        return candidate

    @staticmethod
    def _canonical_path(value: str) -> tuple[str, str]:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.fragment or value.startswith("/"):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "HTTP path is not relative")
        decoded = unquote(parsed.path, errors="strict")
        if unquote(decoded, errors="strict") != decoded:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "HTTP path contains unstable percent encoding",
            )
        if "\\" in decoded:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "HTTP path contains backslash")
        segments = decoded.rstrip("/").split("/")
        if not segments or any(segment in {"", ".", ".."} for segment in segments):
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "HTTP path contains a noncanonical segment",
            )
        return "/".join(segments), parsed.query

    def _open(self, url: str, method: str) -> tuple[bytes, dict[str, str], int]:
        try:
            with self._opener.open(
                Request(url, method=method),
                timeout=self._timeout,
            ) as response:
                headers = {key.casefold(): value for key, value in response.headers.items()}
                raw = b"" if method == "HEAD" else response.read(self._max_bytes + 1)
                return raw, headers, int(response.status)
        except HTTPError as exc:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED
                if 300 <= exc.code < 400
                else ConnectorErrorCode.SOURCE_NOT_FOUND,
                "HTTP connector request was rejected",
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ConnectorFailure(
                ConnectorErrorCode.DRIVER_ERROR,
                "HTTP connector request failed",
            ) from exc

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        url = self._url(request)
        _raw, headers, _status = await asyncio.to_thread(self._open, url, "HEAD")
        content_type = headers.get("content-type", "").split(";", 1)[0]
        if content_type not in self._types:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "HTTP content type is not allowed",
            )
        etag = headers.get("etag")
        modified = headers.get("last-modified")
        version = NativeVersion(
            kind=NativeVersionKind.ETAG if etag else NativeVersionKind.SNAPSHOT,
            value=etag or modified or "HEAD_WITHOUT_VERSION",
        )
        size = headers.get("content-length")
        return (
            ConnectorArtifactRef(
                source_uri=url,
                locator={"relative_path": str(request.selector["relative_path"])},
                media_type=content_type,
                native_version=version,
                size_hint=None if size is None else int(size),
                observed_at=utc_now(),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del ref, checkpoint
        url = self._url(request)
        raw, _headers, _status = await asyncio.to_thread(self._open, url, "GET")
        limit = min(request.max_bytes, self._max_bytes)
        if len(raw) > limit:
            raise ConnectorFailure(
                ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED,
                "HTTP response exceeds byte limit",
            )
        discovered = (await self.discover(request))[0]
        return ConnectorFetchResult(
            ref=discovered,
            raw=raw,
            content_sha256=content_digest(raw),
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
