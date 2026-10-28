"""Korean Windows command output is decoded independently of Python UTF-8 mode."""

import io
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


@pytest.fixture(autouse=True)
def forget_sid() -> Generator[None]:
    """The SID is kept for the process; these tests stand in for whoami, so start without one."""
    windows_user_sid.forget_user_sid()
    yield
    windows_user_sid.forget_user_sid()


def exercise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["xai", "claude"],
    *,
    korean_user: bool = True,
    valid_sid: bool = True,
    acl_code: int = 0,
) -> tuple[list[list[str]], list[BaseException], ModelExecutionHold | None]:
    module = xai_profile if provider == "xai" else claude_profile
    profile = (
        xai_profile.XaiProfile(tmp_path)
        if provider == "xai"
        else claude_profile.ClaudeProfile(tmp_path)
    )
    calls: list[list[str]] = []
    reader_errors: list[BaseException] = []
    username = "HOST\\홍길동" if korean_user else "HOST\\fixture"
    whoami = f'"{username}","{SID if valid_sid else "invalid-sid"}"\r\n'.encode("cp949")
    acl = "파일 처리 완료\r\n".encode("cp949")

    def run(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
        creationflags: int,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check
        assert creationflags == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        calls.append(command)
        assert command[0] in {"whoami", "icacls"}
        assert timeout == (3 if command[0] == "whoami" else 5)
        raw = whoami if command[0] == "whoami" else acl
        # Simulate a Korean OEM code page and a UTF-8 default, including Windows'
        # threaded pipe reader. No native command or permission change occurs.
        decoder = "cp949" if encoding == "oem" else encoding or "utf-8"
        chunks: list[str] = []

        def read() -> None:
            try:
                with io.TextIOWrapper(io.BytesIO(raw), encoding=decoder, errors=errors) as stream:
                    chunks.append(stream.read())
            except UnicodeError as exc:
                reader_errors.append(exc)

        thread = threading.Thread(target=read)
        thread.start()
        thread.join()
        return subprocess.CompletedProcess(
            command,
            0 if command[0] == "whoami" else acl_code,
            stdout=chunks[0] if chunks else None,
            stderr="",
        )

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess, "run", run)
    failure: ModelExecutionHold | None = None
    try:
        profile.prepare()
    except ModelExecutionHold as exc:
        failure = exc
    return calls, reader_errors, failure


@pytest.mark.parametrize("provider", ["xai", "claude"])
@pytest.mark.parametrize("korean_user", [True, False])
def test_windows_native_output_preserves_sid_without_reader_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["xai", "claude"],
    korean_user: bool,
) -> None:
    calls, reader_errors, failure = exercise(
        tmp_path, monkeypatch, provider, korean_user=korean_user
    )
    assert reader_errors == []
    assert failure is None
    assert calls[0] == ["whoami", "/user", "/fo", "csv", "/nh"]
    assert calls[1][2:] == ["/inheritance:r", "/grant:r", f"*{SID}:(OI)(CI)F"]


@pytest.mark.parametrize("provider", ["xai", "claude"])
@pytest.mark.parametrize("invalid_sid", [True, False])
def test_decoding_does_not_relax_permission_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["xai", "claude"],
    invalid_sid: bool,
) -> None:
    calls, reader_errors, failure = exercise(
        tmp_path, monkeypatch, provider, valid_sid=not invalid_sid, acl_code=0 if invalid_sid else 1
    )
    assert reader_errors == []
    assert failure is not None
    provider_name = provider.upper()
    assert str(failure) == f"{provider_name}_PROFILE_PERMISSION_UNAVAILABLE"
    assert len(calls) == (1 if invalid_sid else 2)
