"""One THOTH process per workspace (D-A): refusal, release, and what the refusal never writes.

Children are real processes with a synthetic home; no model, login or network is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from tests.integration.test_workspace_runtime_exclusivity_baseline import (
    _HOLDER,  # pyright: ignore[reportPrivateUsage]
    _child_env,  # pyright: ignore[reportPrivateUsage]
)
from typer.testing import CliRunner, Result

from thoth.adapters.http.app import create_app
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.adapters.storage.workspace_owner import (
    LOCK_FILE,
    WorkspaceHolder,
    WorkspaceInUse,
    acquire_workspace_owner,
)
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.cli import app as cli_app

_LOCK_HOLDER = r"""
import os, sys
from pathlib import Path
from thoth.adapters.storage.workspace_owner import acquire_workspace_owner

workspace, marker = Path(sys.argv[1]), Path(sys.argv[2])
ownership = acquire_workspace_owner(workspace, command="thoth serve")
marker.write_text(str(os.getpid()), encoding="utf-8")
sys.stdin.readline()
ownership.release()
"""

_TRY_ACQUIRE = r"""
import sys
from pathlib import Path
from thoth.adapters.storage.workspace_owner import WorkspaceInUse, acquire_workspace_owner

try:
    acquire_workspace_owner(Path(sys.argv[1])).release()
    print("OK")
except WorkspaceInUse:
    print("IN_USE")
"""


@contextmanager
def _holder(tmp_path: Path, workspace: Path, script: str, *, kill: bool = False) -> Generator[int]:
    marker = tmp_path / f"holder-{abs(hash(script)) % 10**6}.marker"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    with (tmp_path / "holder.log").open("wb") as sink:
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(workspace), str(marker)],
            env=_child_env(home),
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=sink,
            text=True,
        )
    try:
        deadline = time.monotonic() + 90
        while not marker.is_file() and time.monotonic() < deadline:
            assert child.poll() is None, (tmp_path / "holder.log").read_text(errors="replace")[
                -1500:
            ]
            time.sleep(0.05)
        assert marker.is_file()
        yield int(marker.read_text(encoding="utf-8"))
    finally:
        if kill and child.poll() is None:
            child.kill()
        elif child.poll() is None and child.stdin is not None:
            child.stdin.write("\n")
            child.stdin.flush()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


def _can_acquire(tmp_path: Path, workspace: Path) -> bool:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    completed = subprocess.run(
        [sys.executable, "-B", "-c", _TRY_ACQUIRE, str(workspace)],
        env=_child_env(home),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.stdout.strip() in {"OK", "IN_USE"}, completed.stderr[-1500:]
    return completed.stdout.strip() == "OK"


def test_refusal_names_the_other_process_in_korean_and_english(tmp_path: Path) -> None:
    holder = WorkspaceHolder(
        process_id=4242, started_at="2026-10-17 09:12 UTC", command="thoth serve"
    )
    error = WorkspaceInUse(tmp_path / "ws", holder)
    assert "이미 다른 THOTH가 사용 중" in error.message_ko
    assert "PID 4242" in error.message_ko and "thoth serve" in error.message_ko
    assert "먼저 종료한 뒤 다시 실행하세요" in error.message_ko
    assert "already in use by another THOTH" in error.message_en
    assert "PID 4242" in error.message_en and "Close that one first" in error.message_en
    assert str(tmp_path / "ws") in error.message_ko and str(tmp_path / "ws") in error.message_en
    bare = WorkspaceInUse(tmp_path / "ws", None)
    assert "PID" not in bare.message_ko and "PID" not in bare.message_en
    assert "이미 다른 THOTH가 사용 중입니다." in bare.message_ko


def test_one_process_shares_the_lock_by_reference_count(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first = acquire_workspace_owner(workspace)
    second = acquire_workspace_owner(workspace)
    try:
        assert not _can_acquire(tmp_path, workspace)
        first.release()
        first.release()  # releasing twice must not give away the second reference
        assert not _can_acquire(tmp_path, workspace)
    finally:
        second.release()
    assert _can_acquire(tmp_path, workspace)
    assert (workspace / LOCK_FILE).stat().st_size == 1  # the hint is cleared on a clean release


def _hint_bytes(workspace: Path) -> bytes:
    """Read the lock file from byte 1: byte 0 is the lock itself and cannot be read while held."""
    with (workspace / LOCK_FILE).open("rb") as handle:
        handle.seek(1)
        return handle.read()


def _create_runtime_once_the_os_frees_the_lock(workspace: Path) -> AppRuntime:
    """Windows releases a killed process's lock a few milliseconds after the process is gone."""
    deadline = time.monotonic() + 10
    while True:
        try:
            return create_runtime(workspace)
        except WorkspaceInUse:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def _tables(database: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        names = [
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        return {
            name: int(connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
            for name in names
        }


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != LOCK_FILE
    }


def test_a_refused_second_process_writes_nothing_to_the_first_ones_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    with _holder(tmp_path, workspace, _HOLDER) as holder_pid:
        database = workspace / "db" / "thoth.sqlite3"
        watcher = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            version = watcher.execute("PRAGMA data_version").fetchone()[0]
            tables, files = _tables(database), _file_hashes(workspace)
            with pytest.raises(WorkspaceInUse) as refused:
                create_runtime(workspace)
            assert refused.value.holder is not None
            assert refused.value.holder.process_id == holder_pid
            assert watcher.execute("PRAGMA data_version").fetchone()[0] == version
            assert _tables(database) == tables
            assert (
                _file_hashes(workspace) == files
            )  # database, WAL, objects, diagnostics: unchanged
        finally:
            watcher.close()
        # The process id and path are only in the lock file, which is the one place they may be.
        hint = json.loads(_hint_bytes(workspace).decode("utf-8"))
        assert hint["pid"] == holder_pid


def test_a_killed_holder_frees_the_workspace_and_its_stale_lock_file_does_not_block(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    with (
        _holder(tmp_path, workspace, _LOCK_HOLDER, kill=True) as holder_pid,
        pytest.raises(WorkspaceInUse),
    ):
        acquire_workspace_owner(workspace)
    stale = _hint_bytes(workspace)
    assert str(holder_pid).encode() in stale  # the killed process never cleared its hint
    runtime = _create_runtime_once_the_os_frees_the_lock(workspace)
    try:
        assert not _can_acquire(tmp_path, workspace)
        assert str(os.getpid()).encode() in _hint_bytes(workspace)
    finally:
        runtime.close()
    assert _can_acquire(tmp_path, workspace)


def test_closing_a_runtime_releases_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = create_runtime(workspace)
    assert not _can_acquire(tmp_path, workspace)
    runtime.close()
    assert _can_acquire(tmp_path, workspace)


def test_a_failed_start_releases_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"

    def broken(_database: Path) -> None:
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr("thoth.adapters.storage.bundle.migrate_sqlite_database", broken)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        create_runtime(workspace)
    monkeypatch.undo()
    assert _can_acquire(tmp_path, workspace)
    create_runtime(workspace).close()


def test_the_hosted_review_mode_is_not_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    with _holder(tmp_path, workspace, _LOCK_HOLDER):
        monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
        stores = SqliteStoreFactory().open(workspace)
        stores.close()


def _blocked_cli(tmp_path: Path, arguments: list[str], monkeypatch: pytest.MonkeyPatch) -> Result:
    import uvicorn

    def never_listens(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a refused server must not start listening")

    monkeypatch.setattr(uvicorn, "run", never_listens)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "ready-1",
                "method": "workspace/ready",
                "params": {"_meta": {"idempotencyKey": "locked-run"}, "input": {}},
            }
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    expanded = [
        part.replace("<WS>", str(workspace)).replace("<REQ>", str(request)) for part in arguments
    ]
    with _holder(tmp_path, workspace, _LOCK_HOLDER):
        return CliRunner().invoke(cli_app, expanded, input="/quit\n")


@pytest.mark.parametrize(
    "arguments",
    [
        ["serve", "--workspace", "<WS>"],
        ["desktop", "--workspace", "<WS>"],
        ["events", "--project-id", "p", "--operation-id", "o", "--workspace", "<WS>"],
        ["run", "--request", "<REQ>", "--workspace", "<WS>"],
        ["rpc", "--workspace", "<WS>"],
        ["--workspace", "<WS>"],
    ],
    ids=["serve", "desktop", "events", "run", "rpc", "shell"],
)
def test_every_command_that_opens_the_database_is_refused_with_code_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    result = _blocked_cli(tmp_path, arguments, monkeypatch)
    assert result.exit_code == 3, result.output
    shown = result.output
    assert "이미 다른 THOTH가 사용 중입니다" in shown
    assert "already in use by another THOTH" in shown
    assert not (tmp_path / "workspace" / "db").exists()  # refused before any migration started


def test_serve_owns_the_workspace_before_it_listens_and_lets_go_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    workspace = tmp_path / "workspace"
    observed: list[bool] = []

    def listens(*_args: object, **_kwargs: object) -> None:
        observed.append(_can_acquire(tmp_path, workspace))  # owned before the first request
        runtime = create_runtime(workspace)  # the first request, in the same process
        runtime.close()
        observed.append(_can_acquire(tmp_path, workspace))  # serve still owns it

    monkeypatch.setattr(uvicorn, "run", listens)
    result = CliRunner().invoke(cli_app, ["serve", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert observed == [False, False]
    assert _can_acquire(tmp_path, workspace)


async def test_a_server_not_started_by_the_cli_answers_503_until_the_workspace_is_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    monkeypatch.delenv("THOTH_DEPLOYMENT_MODE", raising=False)
    application = create_app()
    body = {
        "jsonrpc": "2.0",
        "id": "ready-1",
        "method": "workspace/ready",
        "params": {"_meta": {"idempotencyKey": "locked-http"}, "input": {}},
    }
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as client:
            with _holder(tmp_path, workspace, _LOCK_HOLDER):
                refused = await client.post("/rpc", json=body)
                assert refused.status_code == 503
                detail = refused.json()["detail"]
                assert detail["code"] == "WORKSPACE_IN_USE"
                assert "이미 다른 THOTH가 사용 중입니다" in detail["message_ko"]
                assert "already in use by another THOTH" in detail["message_en"]
                assert application.state.runtime is None
            allowed = await client.post("/rpc", json=body)
            assert allowed.status_code == 200
            assert application.state.runtime is not None
    finally:
        if application.state.runtime is not None:
            application.state.runtime.close()
