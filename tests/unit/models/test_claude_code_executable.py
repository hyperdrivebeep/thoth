"""Finding the official Claude Code executable, including the npm shim on Windows."""

from __future__ import annotations

from pathlib import Path

import pytest

from thoth.adapters.models.claude_code import ClaudeCodeUnavailable, resolve_claude_executable

_SHIM = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\n"
    "CALL :find_dp0\r\n"
    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n'
)
_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _env(path: Path, home: Path | None = None) -> dict[str, str]:
    values = {"PATH": str(path), "PATHEXT": _PATHEXT}
    if home is not None:
        values["USERPROFILE"] = str(home)
    return values


def _npm_install(directory: Path, *, with_native: bool = True) -> Path:
    directory.mkdir(parents=True)
    shim = directory / "claude.cmd"
    shim.write_text(_SHIM, encoding="utf-8", newline="")
    native = directory / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if with_native:
        native.parent.mkdir(parents=True)
        native.write_bytes(b"synthetic-native-never-executed")
    return native


def test_windows_npm_shim_resolves_to_the_native_executable(tmp_path: Path) -> None:
    native = _npm_install(tmp_path / "npm")
    found = resolve_claude_executable(env=_env(tmp_path / "npm"), platform="win32")
    assert found == native.resolve()


def test_explicit_shim_path_resolves_to_the_native_executable(tmp_path: Path) -> None:
    native = _npm_install(tmp_path / "npm")
    found = resolve_claude_executable(
        tmp_path / "npm" / "claude.cmd", env=_env(tmp_path), platform="win32"
    )
    assert found == native.resolve()


def test_shim_without_its_native_target_still_requires_the_native_binary(tmp_path: Path) -> None:
    _npm_install(tmp_path / "npm", with_native=False)
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_NATIVE_BINARY_REQUIRED"):
        resolve_claude_executable(env=_env(tmp_path / "npm"), platform="win32")


def test_native_executable_on_path_is_used_directly(tmp_path: Path) -> None:
    directory = tmp_path / "bin"
    directory.mkdir()
    native = directory / "claude.exe"
    native.write_bytes(b"synthetic-native-never-executed")
    found = resolve_claude_executable(env=_env(directory), platform="win32")
    assert found == native.resolve()


def test_official_installer_location_is_a_fallback_when_path_has_no_claude(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    native = home / ".local" / "bin" / "claude.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"synthetic-native-never-executed")
    empty = tmp_path / "empty"
    empty.mkdir()
    found = resolve_claude_executable(env=_env(empty, home), platform="win32")
    assert found == native.resolve()


def test_path_wins_over_the_installer_location(tmp_path: Path) -> None:
    native = _npm_install(tmp_path / "npm")
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "claude.exe").write_bytes(b"other")
    found = resolve_claude_executable(env=_env(tmp_path / "npm", home), platform="win32")
    assert found == native.resolve()


def test_missing_executable_is_reported_as_not_installed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_NOT_INSTALLED"):
        resolve_claude_executable(env=_env(empty, tmp_path / "no-home"), platform="win32")


def test_thoth_tools_folder_is_used_when_path_has_no_claude(tmp_path: Path) -> None:
    local = tmp_path / "local"
    native = (
        local
        / "THOTH"
        / "tools"
        / "claude-code"
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"synthetic-native-never-executed")
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {**_env(empty, tmp_path / "no-home"), "LOCALAPPDATA": str(local)}
    assert resolve_claude_executable(env=env, platform="win32") == native.resolve()


def test_the_thoth_tools_folder_wins_over_a_claude_on_path(tmp_path: Path) -> None:
    _npm_install(tmp_path / "npm")
    local = tmp_path / "local"
    tools_native = (
        local
        / "THOTH"
        / "tools"
        / "claude-code"
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    tools_native.parent.mkdir(parents=True)
    tools_native.write_bytes(b"other")
    env = {**_env(tmp_path / "npm"), "LOCALAPPDATA": str(local)}
    assert resolve_claude_executable(env=env, platform="win32") == tools_native.resolve()


def test_path_is_used_when_the_thoth_tools_folder_has_no_claude(tmp_path: Path) -> None:
    global_native = _npm_install(tmp_path / "npm")
    env = {**_env(tmp_path / "npm"), "LOCALAPPDATA": str(tmp_path / "local")}
    assert resolve_claude_executable(env=env, platform="win32") == global_native.resolve()
