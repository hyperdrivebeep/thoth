"""Tool installers run through a fake npm; nothing is installed and no provider is named in core."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from thoth.adapters.models.claude_code_tooling import (
    CLAUDE_CODE_PACKAGE_SPEC,
    ClaudeCodeToolInstaller,
)
from thoth.adapters.models.codex_profile import CodexExecutableIdentity, CodexProfileHold
from thoth.adapters.models.codex_tooling import CodexToolInstaller
from thoth.adapters.models.tool_installers import LocalModelTooling, ToolInstallerRegistry
from thoth.ports.model_tooling import ModelToolingError


class FakeNpm:
    def __init__(self, *, returncode: int = 0, timeout: bool = False) -> None:
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self._returncode, self._timeout = returncode, timeout

    def __call__(self, argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, timeout))
        if self._timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self._returncode, "", "npm private diagnostic")


def _identity(version: str = "codex-cli 0.157.1") -> CodexExecutableIdentity:
    return CodexExecutableIdentity(Path("codex.exe"), Path("pkg"), version, "d" * 64, "w", "p")


def test_codex_install_uses_one_pinned_npm_command_and_then_verifies(tmp_path: Path) -> None:
    npm, verified = FakeNpm(), list[bool]()

    def verify() -> CodexExecutableIdentity:
        verified.append(True)
        return _identity()

    prefix = tmp_path / "tools" / "codex"
    result = CodexToolInstaller(
        None, runner=npm, npm="npm.cmd", prefix=prefix, verify=verify
    ).install()
    assert npm.calls == [
        (
            (
                "npm.cmd",
                "install",
                "--prefix",
                str(prefix),
                "--install-strategy=nested",
                "--save-exact",
                "@openai/codex@0.157.1",
            ),
            300.0,
        )
    ]
    assert verified == [True]
    assert result == {"installed": True, "version": "codex-cli 0.157.1"}


def test_codex_install_without_npm_fails_before_running_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("thoth.adapters.models.tool_installers.find_npm", lambda: None)
    npm = FakeNpm()
    with pytest.raises(ModelToolingError, match="NODE_NPM_UNAVAILABLE"):
        CodexToolInstaller(None, runner=npm, prefix=tmp_path, verify=_identity).install()
    assert npm.calls == []


@pytest.mark.parametrize(
    ("npm", "reason"),
    [
        (FakeNpm(timeout=True), "CODEX_TOOL_INSTALL_TIMEOUT"),
        (FakeNpm(returncode=1), "CODEX_TOOL_INSTALL_FAILED"),
    ],
)
def test_codex_install_failures_are_typed_and_do_not_leak_npm_output(
    tmp_path: Path, npm: FakeNpm, reason: str
) -> None:
    with pytest.raises(ModelToolingError) as caught:
        CodexToolInstaller(
            None, runner=npm, npm="npm.cmd", prefix=tmp_path, verify=_identity
        ).install()
    assert str(caught.value) == reason


def test_codex_install_reports_the_verification_reason_when_the_version_is_wrong(
    tmp_path: Path,
) -> None:
    def wrong_version() -> CodexExecutableIdentity:
        raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")

    with pytest.raises(ModelToolingError, match="CODEX_STANDALONE_PIN_UNAVAILABLE"):
        CodexToolInstaller(
            None, runner=FakeNpm(), npm="npm.cmd", prefix=tmp_path, verify=wrong_version
        ).install()


def test_claude_code_install_targets_its_own_folder_and_clears_the_cached_status(
    tmp_path: Path,
) -> None:
    npm, cleared = FakeNpm(), list[Path | None]()
    local = tmp_path / "local"
    installer = ClaudeCodeToolInstaller(
        tmp_path / "workspace",
        runner=npm,
        npm="npm.cmd",
        env={"LOCALAPPDATA": str(local)},
        verify=lambda: {"connection_state": "LOGIN_REQUIRED"},
        invalidate=lambda workspace: cleared.append(workspace),
    )
    result = installer.install()
    argv, timeout = npm.calls[0]
    assert argv[3] == str(local / "THOTH" / "tools" / "claude-code")
    assert argv[-1] == CLAUDE_CODE_PACKAGE_SPEC == "@anthropic-ai/claude-code@2.1.284"
    assert "-g" not in argv and "--global" not in argv and timeout == 300.0
    assert cleared == [tmp_path / "workspace"]
    assert result == {"installed": True, "version": "2.1.284"}


def test_claude_code_install_reports_an_unusable_result_instead_of_success(
    tmp_path: Path,
) -> None:
    installer = ClaudeCodeToolInstaller(
        None,
        runner=FakeNpm(),
        npm="npm.cmd",
        prefix=tmp_path,
        verify=lambda: {
            "connection_state": "UNAVAILABLE",
            "reason_code": "CLAUDE_CODE_CAPABILITY_UNSUPPORTED",
        },
        invalidate=lambda _workspace: None,
    )
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_CAPABILITY_UNSUPPORTED"):
        installer.install()


def test_a_new_tool_is_added_by_registration_without_changing_the_tooling_port() -> None:
    class Fake:
        def install(self) -> dict[str, object]:
            return {"installed": True, "version": "1.0"}

    registry = ToolInstallerRegistry()
    registry.register("fake-tool", Fake())
    tooling = LocalModelTooling(registry)
    assert tooling.install("fake-tool") == {
        "installed": True,
        "version": "1.0",
        "tool_id": "fake-tool",
    }
    with pytest.raises(ModelToolingError, match="MODEL_TOOL_UNKNOWN"):
        tooling.install("missing")
    with pytest.raises(ValueError, match="TOOL_INSTALLER_DUPLICATE"):
        registry.register("fake-tool", Fake())


def test_a_second_install_of_the_same_tool_is_refused_while_the_first_runs() -> None:
    started, release = threading.Event(), threading.Event()

    class Slow:
        def install(self) -> dict[str, object]:
            started.set()
            assert release.wait(3.0)
            return {"installed": True}

    registry = ToolInstallerRegistry()
    registry.register("slow", Slow())
    tooling = LocalModelTooling(registry)
    first = threading.Thread(target=tooling.install, args=("slow",))
    first.start()
    assert started.wait(3.0)
    with pytest.raises(ModelToolingError, match="MODEL_TOOL_INSTALL_IN_PROGRESS"):
        tooling.install("slow")
    release.set()
    first.join(3.0)
    assert tooling.install("slow")["installed"] is True
