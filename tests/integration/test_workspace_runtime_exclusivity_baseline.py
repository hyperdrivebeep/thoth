"""Who may open a workspace: the single-runtime rule and what stays as it was (V-A, then D-A).

 - a second PROCESS is refused when another process already has the workspace open
   (before D-A this test pinned that it was allowed);
 - several runtimes inside ONE process share a workspace;
 - the local server opens its runtime on the first request, not at start-up;
 - the commands that do not use the database leave no database behind, while `events` and
   `run` open a runtime.
No model, login, network or real credential is used; child processes get a synthetic home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from thoth.adapters.http.app import create_app
from thoth.adapters.storage.workspace_owner import WorkspaceInUse
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.cli import app as cli_app

ROOT = Path(__file__).resolve().parents[2]

_HOLDER = r"""
import os
import sys
from pathlib import Path
from thoth.apps.runtime import create_runtime

workspace, marker = Path(sys.argv[1]), Path(sys.argv[2])
runtime = create_runtime(workspace)
marker.write_text(str(os.getpid()), encoding="utf-8")
sys.stdin.readline()
runtime.close()
"""


def _child_env(home: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "local"),
        "APPDATA": str(home / "roaming"),
        "XDG_DATA_HOME": str(home / "xdg"),
        "CODEX_HOME": str(home / "codex"),
    }


def _projects(runtime: AppRuntime) -> int:
    with runtime.ledger.engine.connect() as connection:
        return int(connection.exec_driver_sql("SELECT count(*) FROM projects").scalar())


def test_a_second_process_cannot_open_a_workspace_another_process_has_open(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    marker = tmp_path / "holder-open.marker"
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "holder.log"
    # The child's migration log is long; a full pipe would block it, so write it to a file.
    with log.open("wb") as sink:
        holder = subprocess.Popen(
            [sys.executable, "-B", "-c", _HOLDER, str(workspace), str(marker)],
            env=_child_env(home),
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=sink,
            text=True,
        )
    try:
        deadline = time.monotonic() + 60
        while not marker.is_file() and time.monotonic() < deadline:
            assert holder.poll() is None, log.read_text(encoding="utf-8", errors="replace")[-2000:]
            time.sleep(0.05)
        assert marker.is_file()
        with pytest.raises(WorkspaceInUse) as refused:
            create_runtime(workspace)
        assert refused.value.holder is not None
        # On Windows the launched process can be a launcher, so compare with the child's own id.
        assert refused.value.holder.process_id == int(marker.read_text(encoding="utf-8"))
        assert holder.poll() is None
    finally:
        if holder.poll() is None and holder.stdin is not None:
            holder.stdin.write("\n")
            holder.stdin.flush()
        try:
            holder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=10)
    assert holder.returncode == 0, log.read_text(encoding="utf-8", errors="replace")[-2000:]


def test_runtimes_in_one_process_share_a_workspace_until_each_is_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first = create_runtime(workspace)
    second = create_runtime(workspace)
    try:
        assert _projects(first) == _projects(second) == 0
        assert first.bus.registered_methods() == second.bus.registered_methods()
        first.close()
        assert _projects(second) == 0
    finally:
        second.close()
    reopened = create_runtime(workspace)
    try:
        assert _projects(reopened) == 0
    finally:
        reopened.close()


async def test_the_local_server_opens_its_runtime_on_the_first_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "server-workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    monkeypatch.delenv("THOTH_DEPLOYMENT_MODE", raising=False)
    application = create_app()
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as client:
            assert (await client.get("/healthz")).status_code == 200
            assert application.state.runtime is None
            assert not (workspace / "db").exists()
            response = await client.post(
                "/rpc",
                json={
                    "jsonrpc": "2.0",
                    "id": "ready-1",
                    "method": "workspace/ready",
                    "params": {"_meta": {"idempotencyKey": "baseline-ready"}, "input": {}},
                },
            )
            assert response.status_code == 200
            assert application.state.runtime is not None
            assert (workspace / "db" / "thoth.sqlite3").is_file()
    finally:
        if application.state.runtime is not None:
            application.state.runtime.close()


def _git_repository(path: Path) -> Path:
    path.mkdir()
    identity = ["-c", "user.name=synthetic", "-c", "user.email=synthetic@example.invalid"]
    for arguments in (
        ["init", "-q"],
        [*identity, "commit", "--allow-empty", "-q", "-m", "synthetic"],
    ):
        subprocess.run(["git", "-C", str(path), *arguments], check=True, capture_output=True)
    return path


def test_commands_that_do_not_use_the_database_leave_no_database(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "note.txt"
    source.write_text("synthetic source", encoding="utf-8")
    runner = CliRunner()
    shown = runner.invoke(cli_app, ["workspace", "--workspace", str(workspace)])
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["workspace"] == str(workspace.resolve())
    runner.invoke(cli_app, ["doctor", "--workspace", str(workspace), "--json"])
    staged = runner.invoke(
        cli_app, ["source-stage", "--source", str(source), "--workspace", str(workspace)]
    )
    assert staged.exit_code == 0
    snapshot = runner.invoke(
        cli_app,
        [
            "git-snapshot",
            "--repository",
            str(_git_repository(tmp_path / "repository")),
            "--workspace",
            str(workspace),
        ],
    )
    assert snapshot.exit_code == 0
    assert (workspace / "inbox").is_dir()
    assert not (workspace / "db").exists()


def test_events_and_run_open_a_runtime_and_create_the_database(tmp_path: Path) -> None:
    runner = CliRunner()
    events_workspace = tmp_path / "events"
    runner.invoke(
        cli_app,
        [
            "events",
            "--project-id",
            "project:absent",
            "--operation-id",
            "operation:absent",
            "--workspace",
            str(events_workspace),
        ],
    )
    assert (events_workspace / "db" / "thoth.sqlite3").is_file()
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "ready-1",
                "method": "workspace/ready",
                "params": {"_meta": {"idempotencyKey": "baseline-run"}, "input": {}},
            }
        ),
        encoding="utf-8",
    )
    run_workspace = tmp_path / "run"
    result = runner.invoke(
        cli_app, ["run", "--request", str(request), "--workspace", str(run_workspace)]
    )
    assert result.exit_code == 0
    assert (run_workspace / "db" / "thoth.sqlite3").is_file()
