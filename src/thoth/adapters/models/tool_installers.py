"""Registry of THOTH-owned tool installers; core code only ever passes a `tool_id`.

Every installer writes into its own folder under `%LOCALAPPDATA%\\THOTH\\tools`. None of them
touches a global install, a login profile, or another program's files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from thoth.ports.model_tooling import ModelToolingError, ModelToolingPort

NPM_INSTALL_TIMEOUT_SECONDS = 300.0


class ToolInstaller(Protocol):
    def install(self) -> dict[str, object]: ...


NpmRunner = Callable[[tuple[str, ...], float], "subprocess.CompletedProcess[str]"]


def tools_prefix(name: str, env: Mapping[str, str] | None = None) -> Path:
    """The THOTH-owned folder for one tool."""
    base = (os.environ if env is None else env).get("LOCALAPPDATA")
    if not base:
        raise ModelToolingError("MODEL_TOOL_PREFIX_UNAVAILABLE")
    return Path(base) / "THOTH" / "tools" / name


def find_npm() -> str | None:
    return shutil.which("npm.cmd" if sys.platform == "win32" else "npm")


def run_npm(argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def install_npm_package(
    prefix: Path,
    package_spec: str,
    *,
    failed: str,
    timed_out: str,
    runner: NpmRunner = run_npm,
    npm: str | None = None,
    timeout: float = NPM_INSTALL_TIMEOUT_SECONDS,
) -> None:
    """One exact-version npm install into `prefix`; outcome is a typed reason, not npm output."""
    executable = npm or find_npm()
    if not executable:
        raise ModelToolingError("NODE_NPM_UNAVAILABLE")
    argv = (
        executable,
        "install",
        "--prefix",
        str(prefix),
        "--install-strategy=nested",
        "--save-exact",
        package_spec,
    )
    try:
        completed = runner(argv, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ModelToolingError(timed_out) from exc
    except OSError as exc:
        raise ModelToolingError("NODE_NPM_UNAVAILABLE") from exc
    if completed.returncode != 0:
        raise ModelToolingError(failed)


class ToolInstallerRegistry:
    def __init__(self) -> None:
        self._installers: dict[str, ToolInstaller] = {}

    def register(self, tool_id: str, installer: ToolInstaller) -> None:
        if tool_id in self._installers:
            raise ValueError("TOOL_INSTALLER_DUPLICATE")
        self._installers[tool_id] = installer

    def resolve(self, tool_id: str) -> ToolInstaller:
        try:
            return self._installers[tool_id]
        except KeyError as exc:
            raise ModelToolingError("MODEL_TOOL_UNKNOWN") from exc


class LocalModelTooling(ModelToolingPort):
    """Runs one installer at a time per tool; a second request is refused, not queued."""

    def __init__(self, registry: ToolInstallerRegistry) -> None:
        self._registry = registry
        self._guard = threading.Lock()
        self._running: set[str] = set()

    def install(self, tool_id: str) -> dict[str, object]:
        installer = self._registry.resolve(tool_id)
        with self._guard:
            if tool_id in self._running:
                raise ModelToolingError("MODEL_TOOL_INSTALL_IN_PROGRESS")
            self._running.add(tool_id)
        try:
            return {**installer.install(), "tool_id": tool_id}
        finally:
            with self._guard:
                self._running.discard(tool_id)


def default_tool_installer_registry(workspace: Path | None) -> ToolInstallerRegistry:
    from thoth.adapters.models.claude_code_tooling import ClaudeCodeToolInstaller
    from thoth.adapters.models.codex_tooling import CodexToolInstaller

    registry = ToolInstallerRegistry()
    registry.register("codex", CodexToolInstaller(workspace))
    registry.register("claude-code", ClaudeCodeToolInstaller(workspace))
    return registry
