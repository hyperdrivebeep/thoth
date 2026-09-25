from __future__ import annotations

import asyncio
import subprocess
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


class GitReadConnector:
    def __init__(self, allowed_root: Path, *, connector_id: str = "read-only-git-snapshot") -> None:
        self._allowed_root = allowed_root.resolve()
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="GIT",
            driver_version="git-cli:1",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("OS_SESSION", "GIT_CREDENTIAL_HELPER"),
            native_version_kinds=(NativeVersionKind.COMMIT,),
            selector_contract=ConnectorSelectorContract(
                fields=(
                    SelectorFieldSpec(name="repository_path", value_type="STRING"),
                    SelectorFieldSpec(name="revision", value_type="STRING"),
                    SelectorFieldSpec(name="file_path", value_type="STRING"),
                ),
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability

    def _repo(self, request: ConnectorAccessRequest) -> Path:
        relative = Path(selector_text(request.selector, "repository_path"))
        repo = (self._allowed_root / relative).resolve()
        if relative.is_absolute() or not repo.is_relative_to(self._allowed_root):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "repository outside root")
        if not (repo / ".git").exists():
            raise ConnectorFailure(ConnectorErrorCode.SOURCE_NOT_FOUND, "git repository not found")
        return repo

    @staticmethod
    def _git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "git read failed") from exc
        return (
            completed.stdout
            if binary
            else completed.stdout.decode("utf-8", errors="strict").strip()
        )

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        repo = self._repo(request)
        revision = selector_text(request.selector, "revision")
        file_path = selector_text(request.selector, "file_path").replace("\\", "/")
        if file_path.startswith("/") or ".." in Path(file_path).parts:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "git path is outside tree")
        commit = await asyncio.to_thread(
            self._git, repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
        )
        assert isinstance(commit, str)
        return (
            ConnectorArtifactRef(
                source_uri=f"git://{repo.as_posix()}@{commit}/{file_path}",
                locator={
                    "repository_path": repo.relative_to(self._allowed_root).as_posix(),
                    "file_path": file_path,
                },
                media_type=media_type(Path(file_path)),
                native_version=NativeVersion(kind=NativeVersionKind.COMMIT, value=commit),
                observed_at=utc_now(),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        repo = self._repo(request)
        commit = ref.native_version.value
        file_path = str(ref.locator["file_path"])
        if commit is None:
            raise ConnectorFailure(ConnectorErrorCode.UNSUPPORTED_VERSION_TOKEN, "commit missing")
        raw = await asyncio.to_thread(self._git, repo, "show", f"{commit}:{file_path}", binary=True)
        assert isinstance(raw, bytes)
        if len(raw) > request.max_bytes:
            raise ConnectorFailure(ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED, "git blob too large")
        return ConnectorFetchResult(ref=ref, raw=raw, content_sha256=content_digest(raw))

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
