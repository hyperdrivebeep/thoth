from pathlib import Path

import pytest

from thoth.apps.workspace_paths import (
    default_workspace,
    legacy_workspace_candidates,
    selected_workspace,
    workspace_id,
)
from thoth.cli import announce_workspace, workspace_info


def test_windows_default_is_install_and_cwd_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "LocalAppData"
    first = tmp_path / "zip-one"
    second = tmp_path / "zip-two"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    a = default_workspace(platform="win32", environ={"LOCALAPPDATA": str(local)}, home=tmp_path)
    monkeypatch.chdir(second)
    b = default_workspace(platform="win32", environ={"LOCALAPPDATA": str(local)}, home=tmp_path)
    assert a == b == (local / "THOTH").resolve()
    assert not a.exists()


def test_explicit_workspace_and_identity_are_stable_without_migration(tmp_path: Path) -> None:
    old = tmp_path / ".thoth"
    old.mkdir()
    root = selected_workspace(old)
    assert root == old.resolve()
    assert workspace_id(root) == workspace_id(old)
    assert workspace_id(root) != workspace_id(tmp_path / "another")
    assert legacy_workspace_candidates(tmp_path) == (root,)
    assert old.is_dir()


def test_normal_launcher_reports_legacy_resume_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old = tmp_path / ".thoth-local"
    old.mkdir()
    monkeypatch.chdir(tmp_path)
    new_root = tmp_path / "stable-default"
    announce_workspace(new_root, command="thoth serve")
    output = capsys.readouterr()
    assert str(new_root) in output.err
    assert f'thoth serve --workspace "{old}"' in output.err
    assert old.is_dir() and not new_root.exists()
    workspace_info(new_root)
    assert '"selection": "EXPLICIT"' in capsys.readouterr().out
