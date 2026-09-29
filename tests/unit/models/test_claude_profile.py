"""Workspace Claude OAuth secret owner survives reopen and failed atomic replacement."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from thoth.adapters.models import claude_profile
from thoth.adapters.models.claude_profile import ClaudeCredential, ClaudeProfile, ClaudeProfileHold

pytestmark = pytest.mark.usefixtures("xai_http_guard")


def _credential(profile: ClaudeProfile, access: str, generation: str) -> ClaudeCredential:
    return ClaudeCredential(
        access, "synthetic-refresh", time.time() + 3600, generation, profile.profile_id
    )


def test_two_workspaces_and_second_process_read_only_reopen(tmp_path: Path) -> None:
    first = ClaudeProfile(tmp_path / "first")
    second = ClaudeProfile(tmp_path / "second")
    with first.lock():
        first.save(_credential(first, "first-synthetic", "generation-first"))
    assert first.read().generation == "generation-first"  # type: ignore[union-attr]
    assert second.read() is None
    assert first.profile_id != second.profile_id
    script = (
        "import pathlib,sys; from thoth.adapters.models.claude_profile import ClaudeProfile; "
        "p=ClaudeProfile(pathlib.Path(sys.argv[1])); "
        "print(p.read().generation if p.read() is not None else 'MISSING')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "first")],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "generation-first"


def test_failed_atomic_replace_keeps_prior_secret_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = ClaudeProfile(tmp_path)
    with profile.lock():
        profile.save(_credential(profile, "old-synthetic", "generation-old"))
    before = profile.auth_path.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(claude_profile.os, "replace", fail_replace)
    with profile.lock(), pytest.raises(ClaudeProfileHold, match="CLAUDE_AUTH_SAVE_FAILED"):
        profile.save(_credential(profile, "new-synthetic", "generation-new"))
    assert profile.auth_path.read_bytes() == before
    assert profile.read().access_token == "old-synthetic"  # type: ignore[union-attr]
    assert not tuple(profile.root.glob(".claude-*"))
