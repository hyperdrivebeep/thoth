"""The Claude Code install runs no package script; THOTH places the native binary after a check."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from thoth.adapters.models.claude_code_tooling import (
    ClaudeCodeToolInstaller,
    place_native_binary,
)
from thoth.adapters.models.tool_pins import (
    CLAUDE_CODE_PLATFORM_INTEGRITY,
    CLAUDE_CODE_WRAPPER_INTEGRITY,
)
from thoth.ports.model_tooling import ModelToolingError

PLACEHOLDER = b"placeholder"
NATIVE = b"native-binary-bytes"
WRAPPER = "node_modules/@anthropic-ai/claude-code"
PLATFORM = WRAPPER + "/node_modules/@anthropic-ai/claude-code-win32-x64"
OTHER_INTEGRITY = "sha512-" + "A" * 86 + "=="


def _tree(
    prefix: Path,
    *,
    wrapper: str = CLAUDE_CODE_WRAPPER_INTEGRITY,
    platform: str = CLAUDE_CODE_PLATFORM_INTEGRITY,
    platform_file: bool = True,
    lock: bool = True,
) -> Path:
    """An install as npm leaves it when no script ran: the placeholder is still in bin."""
    target = prefix / WRAPPER / "bin" / "claude.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(PLACEHOLDER)
    if platform_file:
        source = prefix / PLATFORM / "claude.exe"
        source.parent.mkdir(parents=True)
        source.write_bytes(NATIVE)
    if lock:
        record = {"packages": {WRAPPER: {"integrity": wrapper}, PLATFORM: {"integrity": platform}}}
        (prefix / "node_modules" / ".package-lock.json").write_text(json.dumps(record), "utf-8")
    return target


def _untouched(target: Path) -> None:
    assert target.read_bytes() == PLACEHOLDER
    assert [item.name for item in target.parent.iterdir()] == ["claude.exe"]


def _npm_ok(argv: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_the_native_binary_replaces_the_placeholder_when_both_integrity_values_match(
    tmp_path: Path,
) -> None:
    target = _tree(tmp_path)
    place_native_binary(tmp_path)
    assert target.read_bytes() == NATIVE
    assert [item.name for item in target.parent.iterdir()] == ["claude.exe"]


@pytest.mark.parametrize("which", ["wrapper", "platform"])
def test_a_different_integrity_value_fails_the_install_and_copies_nothing(
    tmp_path: Path, which: str
) -> None:
    target = _tree(
        tmp_path,
        wrapper=OTHER_INTEGRITY if which == "wrapper" else CLAUDE_CODE_WRAPPER_INTEGRITY,
        platform=OTHER_INTEGRITY if which == "platform" else CLAUDE_CODE_PLATFORM_INTEGRITY,
    )
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_TOOL_INSTALL_FAILED"):
        place_native_binary(tmp_path)
    _untouched(target)


def test_a_missing_platform_binary_fails_the_install_and_copies_nothing(tmp_path: Path) -> None:
    target = _tree(tmp_path, platform_file=False)
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_TOOL_INSTALL_FAILED"):
        place_native_binary(tmp_path)
    _untouched(target)


@pytest.mark.parametrize("damage", ["missing", "not-json", "no-packages"])
def test_a_missing_or_unreadable_install_record_fails_the_install(
    tmp_path: Path, damage: str
) -> None:
    target = _tree(tmp_path, lock=damage != "missing")
    record = tmp_path / "node_modules" / ".package-lock.json"
    if damage == "not-json":
        record.write_text("{", "utf-8")
    if damage == "no-packages":
        record.write_text("[]", "utf-8")
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_TOOL_INSTALL_FAILED"):
        place_native_binary(tmp_path)
    _untouched(target)


def test_a_failed_swap_removes_the_staged_copy_and_keeps_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _tree(tmp_path)

    def refuse(_source: object, _target: object) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr("thoth.adapters.models.claude_code_tooling.os.replace", refuse)
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_TOOL_INSTALL_FAILED"):
        place_native_binary(tmp_path)
    _untouched(target)


def test_the_copy_is_staged_beside_the_target_and_swapped_in_with_one_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _tree(tmp_path)
    seen: list[tuple[Path, Path, bytes]] = []
    real = os.replace

    def spy(source: Path, destination: Path) -> None:
        seen.append((Path(source), Path(destination), Path(source).read_bytes()))
        real(source, destination)

    monkeypatch.setattr("thoth.adapters.models.claude_code_tooling.os.replace", spy)
    place_native_binary(tmp_path)
    [(staged, destination, staged_bytes)] = seen
    assert staged.parent == target.parent and staged != target
    assert destination == target and staged_bytes == NATIVE


def test_an_install_that_already_has_the_native_binary_keeps_it_when_the_check_fails(
    tmp_path: Path,
) -> None:
    target = _tree(tmp_path, platform_file=False)
    target.write_bytes(NATIVE)
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_TOOL_INSTALL_FAILED"):
        place_native_binary(tmp_path)
    assert target.read_bytes() == NATIVE


def test_install_runs_npm_without_scripts_and_then_places_the_binary(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    order: list[str] = []

    def npm(argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        order.append("npm")
        return _npm_ok(argv, timeout)

    def place(prefix: Path) -> None:
        order.append("place")
        assert prefix == tmp_path

    result = ClaudeCodeToolInstaller(
        None,
        runner=npm,
        npm="npm.cmd",
        prefix=tmp_path,
        verify=lambda: {"connection_state": "LOGIN_REQUIRED"},
        invalidate=lambda _workspace: None,
        place=place,
    ).install()
    assert "--ignore-scripts" in calls[0] and calls[0][-1].startswith("@anthropic-ai/claude-code@")
    assert order == ["npm", "place"] and result["installed"] is True


def test_install_does_not_report_success_when_the_integrity_check_fails(tmp_path: Path) -> None:
    target = _tree(tmp_path, platform=OTHER_INTEGRITY)
    checked: list[bool] = []

    def verify() -> dict[str, object]:
        checked.append(True)
        return {"connection_state": "LOGIN_REQUIRED"}

    installer = ClaudeCodeToolInstaller(
        None,
        runner=_npm_ok,
        npm="npm.cmd",
        prefix=tmp_path,
        verify=verify,
        invalidate=lambda _workspace: None,
    )
    with pytest.raises(ModelToolingError, match="CLAUDE_CODE_TOOL_INSTALL_FAILED"):
        installer.install()
    assert checked == []
    _untouched(target)
