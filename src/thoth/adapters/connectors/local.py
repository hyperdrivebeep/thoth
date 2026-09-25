from __future__ import annotations

from pathlib import Path

from thoth.adapters.connectors.common import content_digest, media_type, selector_text, utc_now
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


class LocalFileConnector:
    def __init__(self, root: Path, *, connector_id: str = "local-file-upload") -> None:
        self._root = root.resolve()
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="LOCAL",
            driver_version="1.0.0",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("OS_SESSION",),
            native_version_kinds=(NativeVersionKind.FILE_ID, NativeVersionKind.CONTENT_HASH),
            selector_contract=ConnectorSelectorContract(
                fields=(SelectorFieldSpec(name="relative_path", value_type="STRING"),),
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability

    def _path(self, request: ConnectorAccessRequest) -> Path:
        relative = Path(selector_text(request.selector, "relative_path"))
        path = (self._root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(self._root):
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED, "path is outside connector root"
            )
        if not path.is_file():
            raise ConnectorFailure(ConnectorErrorCode.SOURCE_NOT_FOUND, "source file not found")
        return path

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        path = self._path(request)
        stat = path.stat()
        version = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
        return (
            ConnectorArtifactRef(
                source_uri=f"file://{path.as_posix()}",
                locator={"relative_path": path.relative_to(self._root).as_posix()},
                media_type=media_type(path),
                native_version=NativeVersion(kind=NativeVersionKind.FILE_ID, value=version),
                size_hint=stat.st_size,
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
        path = self._path(request)
        if path.stat().st_size > request.max_bytes:
            raise ConnectorFailure(
                ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED,
                "source exceeds connector byte limit",
            )
        raw = path.read_bytes()
        discovered = (await self.discover(request))[0]
        return ConnectorFetchResult(
            ref=discovered,
            raw=raw,
            content_sha256=content_digest(raw),
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
