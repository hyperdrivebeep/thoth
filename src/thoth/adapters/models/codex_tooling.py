"""Install the pinned Codex CLI into THOTH's own tools folder."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from thoth.adapters.models.codex_profile import (
    PINNED_PACKAGE_VERSION,
    CodexExecutableIdentity,
    CodexProfile,
    CodexProfileHold,
)
from thoth.adapters.models.tool_installers import (
    NpmRunner,
    install_npm_package,
    run_npm,
    tools_prefix,
)
from thoth.ports.model_tooling import ModelToolingError

CODEX_PACKAGE_SPEC = f"@openai/codex@{PINNED_PACKAGE_VERSION}"


def install_pinned_codex(
    prefix: Path, runner: NpmRunner = run_npm, *, npm: str | None = None
) -> None:
    install_npm_package(
        prefix,
        CODEX_PACKAGE_SPEC,
        failed="CODEX_TOOL_INSTALL_FAILED",
        timed_out="CODEX_TOOL_INSTALL_TIMEOUT",
        runner=runner,
        npm=npm,
    )


class CodexToolInstaller:
    """Install, then run the same version and integrity checks the login uses."""

    def __init__(
        self,
        workspace: Path | None,
        *,
        runner: NpmRunner = run_npm,
        npm: str | None = None,
        prefix: Path | None = None,
        verify: Callable[[], CodexExecutableIdentity] | None = None,
    ) -> None:
        self._workspace, self._runner, self._npm = workspace, runner, npm
        self._prefix, self._verify = prefix, verify

    def _identity(self) -> CodexExecutableIdentity:
        if self._verify is not None:
            return self._verify()
        if self._workspace is None:
            raise ModelToolingError("CODEX_WORKSPACE_REQUIRED")
        return CodexProfile.for_workspace(self._workspace).executable()

    def install(self) -> dict[str, object]:
        install_pinned_codex(self._prefix or tools_prefix("codex"), self._runner, npm=self._npm)
        try:
            identity = self._identity()
        except CodexProfileHold as exc:
            raise ModelToolingError(str(exc)) from exc
        return {"installed": True, "version": identity.version}
