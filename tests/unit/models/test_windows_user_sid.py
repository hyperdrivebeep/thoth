"""The Windows user's SID is looked up once per process, not on every profile permission change.

`whoami` is a new process each time and on a loaded machine can outlast its 3 s limit, which made a
profile report that it could not be made private. These tests stand in for `whoami` and `icacls`
(no native command runs, no permission changes) and count how often each is asked.
"""

import subprocess
import threading
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from thoth.adapters.models import claude_profile, windows_user_sid, xai_profile
from thoth.ports.model import ModelExecutionHold

SID = "S-1-5-21-100-200-300-1001"
Provider = Literal["claude", "xai"]


@pytest.fixture(autouse=True)
def forget_sid() -> Generator[None]:
    """The SID is kept for the process. These tests stand in for whoami, so the SID they make up
    must not outlive them: later tests would grant a file to a user that does not exist."""
    windows_user_sid.forget_user_sid()
    yield
    windows_user_sid.forget_user_sid()


class Natives:
    """Stands in for whoami and icacls and records how often each ran."""

    def __init__(self) -> None:
        self.whoami = 0
        self.icacls = 0
        self.failures_left = 0
        self.timeout = False
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release: threading.Event | None = None

    def __call__(self, command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "icacls":
            with self.lock:
                self.icacls += 1
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        with self.lock:
            self.whoami += 1
            failing = self.failures_left > 0
            self.failures_left -= 1 if failing else 0
        self.entered.set()
        if self.release is not None:
            assert self.release.wait(10)
        if failing and self.timeout:
            raise subprocess.TimeoutExpired(command, 3)
        if failing:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command, 0, stdout=f'"HOST\\user","{SID}"\r\n', stderr=""
        )


def windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: Provider
) -> tuple[claude_profile.ClaudeProfile | xai_profile.XaiProfile, Natives]:
    natives = Natives()
    module = claude_profile if provider == "claude" else xai_profile
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess, "run", natives)
    profile = (
        claude_profile.ClaudeProfile(tmp_path)
        if provider == "claude"
        else xai_profile.XaiProfile(tmp_path)
    )
    return profile, natives


@pytest.mark.parametrize("provider", ["claude", "xai"])
def test_repeated_permission_changes_look_the_user_up_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: Provider
) -> None:
    profile, natives = windows(tmp_path, monkeypatch, provider)
    for _ in range(6):
        profile.prepare()
    assert natives.whoami == 1
    assert natives.icacls == 6


@pytest.mark.parametrize("provider", ["claude", "xai"])
@pytest.mark.parametrize("timeout", [False, True])
def test_a_failed_lookup_is_not_kept_and_the_next_call_asks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: Provider, timeout: bool
) -> None:
    profile, natives = windows(tmp_path, monkeypatch, provider)
    natives.failures_left, natives.timeout = 2, timeout
    for _ in range(2):
        with pytest.raises(ModelExecutionHold, match="PROFILE_PERMISSION_UNAVAILABLE"):
            profile.prepare()
    assert natives.whoami == 2 and natives.icacls == 0
    profile.prepare()
    profile.prepare()
    assert natives.whoami == 3 and natives.icacls == 2


def test_concurrent_callers_share_one_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lookup itself is called from many threads at once (profiles are used from worker threads
    # and from the event loop); the profile files are not touched, so nothing else is shared.
    _profile, natives = windows(tmp_path, monkeypatch, "claude")
    natives.release = threading.Event()
    workers = 8
    arrived = threading.Barrier(workers + 1)
    found: list[str] = []
    errors: list[BaseException] = []

    def call() -> None:
        arrived.wait(10)
        try:
            found.append(windows_user_sid.current_user_sid())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(workers)]
    for thread in threads:
        thread.start()
    arrived.wait(10)
    assert natives.entered.wait(10)
    # Give the other callers time to reach the lookup while the first one is still inside it.
    threading.Event().wait(0.2)
    natives.release.set()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert found == [SID] * workers
    assert natives.whoami == 1
