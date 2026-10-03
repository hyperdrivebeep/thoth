"""The Windows supervisor binds a synthetic child before it can run."""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import threading
import time
from collections.abc import Mapping
from ctypes import wintypes
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object only")


def _synthetic_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}


def _is_gone(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return True
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.asyncio
async def test_suspended_job_child_returns_output_without_existing_processes(
    tmp_path: Path,
) -> None:
    from thoth.adapters.models.windows_process_job import spawn_windows_job

    child = spawn_windows_job(
        (sys.executable, "-c", "print('synthetic-job-child')"),
        _synthetic_env(),
        tmp_path,
    )
    output, errors = await child.communicate(b"")
    assert output.strip() == b"synthetic-job-child"
    assert errors == b""
    assert child.returncode == 0


@pytest.mark.asyncio
async def test_job_termination_confirms_synthetic_child_exit(tmp_path: Path) -> None:
    from thoth.adapters.models.windows_process_job import spawn_windows_job

    child = spawn_windows_job(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        _synthetic_env(),
        tmp_path,
    )
    child.kill()
    code = await asyncio.wait_for(child.wait(), 3.0)
    assert code != 0


def test_poll_reports_running_then_the_exit_code_without_blocking(tmp_path: Path) -> None:
    from thoth.adapters.models.windows_process_job import spawn_windows_job

    child = spawn_windows_job(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        _synthetic_env(),
        tmp_path,
    )
    assert child.poll() is None
    child.kill()
    deadline = time.monotonic() + 3.0
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child.poll() not in (None, 0)
    assert child.returncode == child.poll()


@pytest.mark.asyncio
async def test_only_three_stdio_handles_are_inherited(tmp_path: Path) -> None:
    from thoth.adapters.models.windows_process_job import spawn_windows_job

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    unrelated = kernel.CreateEventW(None, False, False, None)
    assert unrelated
    assert kernel.SetHandleInformation(unrelated, 1, 1)
    try:
        code = (
            "import ctypes; from ctypes import wintypes; k=ctypes.WinDLL('kernel32'); "
            "k.GetHandleInformation.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]; "
            "k.GetHandleInformation.restype=wintypes.BOOL; f=wintypes.DWORD(); "
            f"print(int(bool(k.GetHandleInformation(wintypes.HANDLE({int(unrelated)}),ctypes.byref(f)))))"
        )
        child = spawn_windows_job((sys.executable, "-c", code), _synthetic_env(), tmp_path)
        output, errors = await child.communicate(b"")
        assert output.strip() == b"0"
        assert errors == b""
    finally:
        kernel.CloseHandle(unrelated)


@pytest.mark.parametrize("stage", ["assign", "resume"])
def test_suspended_failure_terminates_only_new_child(tmp_path: Path, stage: str) -> None:
    from thoth.adapters.models.windows_process_job import spawn_windows_job

    sentinel = tmp_path / "should-not-run.txt"
    child_code = f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ran')"
    created: list[int] = []
    with pytest.raises(RuntimeError, match=f"SYNTHETIC_{stage.upper()}_FAILURE"):
        spawn_windows_job(
            (sys.executable, "-c", child_code),
            _synthetic_env(),
            tmp_path,
            _fault_at=stage,
            _on_created=created.append,
        )
    assert len(created) == 1 and _is_gone(created[0])
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_job_termination_also_ends_synthetic_grandchild(tmp_path: Path) -> None:
    from thoth.adapters.models.windows_process_job import spawn_windows_job

    marker = tmp_path / "grandchild-pid.txt"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    child = spawn_windows_job((sys.executable, "-c", code), _synthetic_env(), tmp_path)
    deadline = time.monotonic() + 5
    try:
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert marker.exists()
        grandchild_pid = int(marker.read_text())
    finally:
        child.kill()
        await asyncio.wait_for(child.wait(), 3.0)
    assert _is_gone(grandchild_pid)


@pytest.mark.asyncio
async def test_cancel_during_delayed_spawn_claims_and_ends_new_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import thoth.adapters.models.windows_process_job as job_module
    from thoth.adapters.models.claude_code import spawn_claude_process

    real_spawn = job_module.spawn_windows_job
    started = threading.Event()
    release = threading.Event()
    created: list[job_module.WindowsJobProcess] = []

    def delayed_spawn(
        argv: tuple[str, ...], env: Mapping[str, str], cwd: Path
    ) -> job_module.WindowsJobProcess:
        started.set()
        assert release.wait(3)
        process = real_spawn(argv, env, cwd)
        created.append(process)
        return process

    monkeypatch.setattr(job_module, "spawn_windows_job", delayed_spawn)
    task = asyncio.create_task(
        spawn_claude_process(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            _synthetic_env(),
            tmp_path,
        )
    )
    assert await asyncio.to_thread(started.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert len(created) == 1
    assert created[0].returncode is not None and _is_gone(created[0].pid)
