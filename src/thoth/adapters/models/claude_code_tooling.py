"""Install the official Claude Code into THOTH's own tools folder, never globally."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from thoth.adapters.models.claude_code import (
    ClaudeCodeUnavailable,
    claude_code_status,
    invalidate_claude_code_status,
    resolve_claude_executable,
    thoth_claude_code_executable,
)
from thoth.adapters.models.tool_installers import (
    NpmRunner,
    install_npm_package,
    run_npm,
    tools_prefix,
)
from thoth.ports.model_tooling import ModelToolingError

# The version this route was checked against; the executable's own minimum is enforced separately.
CLAUDE_CODE_PACKAGE_SPEC = "@anthropic-ai/claude-code@2.1.284"


def install_pinned_claude_code(
    prefix: Path, runner: NpmRunner = run_npm, *, npm: str | None = None
) -> None:
    install_npm_package(
        prefix,
        CLAUDE_CODE_PACKAGE_SPEC,
        failed="CLAUDE_CODE_TOOL_INSTALL_FAILED",
        timed_out="CLAUDE_CODE_TOOL_INSTALL_TIMEOUT",
        runner=runner,
        npm=npm,
    )


class ClaudeCodeToolInstaller:
    def __init__(
        self,
        workspace: Path | None,
        *,
        runner: NpmRunner = run_npm,
        npm: str | None = None,
        prefix: Path | None = None,
        env: Mapping[str, str] | None = None,
        verify: Callable[[], dict[str, object]] | None = None,
        invalidate: Callable[[Path | None], None] = invalidate_claude_code_status,
    ) -> None:
        self._workspace, self._runner, self._npm = workspace, runner, npm
        self._prefix, self._env, self._verify = prefix, env, verify
        self._invalidate = invalidate

    def _check(self) -> dict[str, object]:
        if self._verify is not None:
            return self._verify()
        if self._workspace is None:
            raise ModelToolingError("CLAUDE_CODE_WORKSPACE_REQUIRED")
        # Check the binary this install just wrote, not whichever claude is first on PATH.
        installed = thoth_claude_code_executable(self._env or os.environ)
        try:
            executable = resolve_claude_executable(installed)
        except ClaudeCodeUnavailable as exc:
            raise ModelToolingError(str(exc)) from exc
        return claude_code_status(self._workspace, executable=executable)

    def install(self) -> dict[str, object]:
        install_pinned_claude_code(
            self._prefix or tools_prefix("claude-code", self._env), self._runner, npm=self._npm
        )
        self._invalidate(self._workspace)
        status = self._check()
        if status.get("connection_state") == "UNAVAILABLE":
            raise ModelToolingError(str(status.get("reason_code") or "CLAUDE_CODE_NOT_INSTALLED"))
        return {"installed": True, "version": CLAUDE_CODE_PACKAGE_SPEC.rsplit("@", 1)[1]}
