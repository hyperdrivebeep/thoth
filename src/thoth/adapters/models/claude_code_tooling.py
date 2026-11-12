"""Install the official Claude Code into THOTH's own tools folder, never globally."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from uuid import uuid4

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
from thoth.adapters.models.tool_pins import (
    CLAUDE_CODE_PLATFORM_INTEGRITY,
    CLAUDE_CODE_VERSION,
    CLAUDE_CODE_WRAPPER_INTEGRITY,
)
from thoth.ports.model_tooling import ModelToolingError

# The version this route was checked against; the executable's own minimum is enforced separately.
CLAUDE_CODE_PACKAGE_SPEC = f"@anthropic-ai/claude-code@{CLAUDE_CODE_VERSION}"

_FAILED = "CLAUDE_CODE_TOOL_INSTALL_FAILED"
_WRAPPER_KEY = "node_modules/@anthropic-ai/claude-code"
# npm nests the platform package under the wrapper (the install strategy), or puts it beside it.
_PLATFORM_KEYS = (
    f"{_WRAPPER_KEY}/node_modules/@anthropic-ai/claude-code-win32-x64",
    "node_modules/@anthropic-ai/claude-code-win32-x64",
)


def _integrity(packages: dict[str, object], key: str) -> object:
    entry = packages.get(key)
    return cast(dict[str, object], entry).get("integrity") if isinstance(entry, dict) else None


def place_native_binary(prefix: Path) -> None:
    """Put the native binary where THOTH runs it, after an install that ran no script.

    The package's own install script only copies the platform package's claude.exe over the
    bin/claude.exe placeholder. THOTH does that copy itself, and only when npm recorded exactly the
    pinned integrity for both the wrapper and the platform package. Anything else (no record, a
    different value, no binary) fails the install and leaves bin/claude.exe as it was. The copy is
    written beside the target under another name and then swapped in.
    """
    try:
        lock = json.loads((prefix / "node_modules" / ".package-lock.json").read_text("utf-8"))
        recorded = lock.get("packages")
    except (OSError, ValueError, AttributeError) as exc:
        raise ModelToolingError(_FAILED) from exc
    if not isinstance(recorded, dict):
        raise ModelToolingError(_FAILED)
    packages = cast(dict[str, object], recorded)
    platform_key = next((key for key in _PLATFORM_KEYS if key in packages), None)
    if (
        platform_key is None
        or _integrity(packages, _WRAPPER_KEY) != CLAUDE_CODE_WRAPPER_INTEGRITY
        or _integrity(packages, platform_key) != CLAUDE_CODE_PLATFORM_INTEGRITY
    ):
        raise ModelToolingError(_FAILED)
    source = prefix / platform_key / "claude.exe"
    target = prefix / _WRAPPER_KEY / "bin" / "claude.exe"
    if source.is_symlink() or not source.is_file() or not target.parent.is_dir():
        raise ModelToolingError(_FAILED)
    staged = target.with_name(f"claude.exe.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, staged)
        os.replace(staged, target)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise ModelToolingError(_FAILED) from exc


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
        place: Callable[[Path], None] = place_native_binary,
    ) -> None:
        self._workspace, self._runner, self._npm = workspace, runner, npm
        self._prefix, self._env, self._verify = prefix, env, verify
        self._invalidate, self._place = invalidate, place

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
        prefix = self._prefix or tools_prefix("claude-code", self._env)
        install_pinned_claude_code(prefix, self._runner, npm=self._npm)
        # The install ran no script, so the native binary is put in place here, after the check.
        self._place(prefix)
        self._invalidate(self._workspace)
        status = self._check()
        if status.get("connection_state") == "UNAVAILABLE":
            raise ModelToolingError(str(status.get("reason_code") or "CLAUDE_CODE_NOT_INSTALLED"))
        return {"installed": True, "version": CLAUDE_CODE_PACKAGE_SPEC.rsplit("@", 1)[1]}
