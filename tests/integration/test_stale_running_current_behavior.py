"""Today's behaviour when the process that was running a general command ends (V-B).

The change that follows (an owner record, then sealing a request whose owner is confirmed gone)
must change exactly what is pinned here and nothing else. Children are real processes killed for
real, with a synthetic home; no model, login or network is used.

Observed today:
 - a request that was interrupted after its domain change was committed stays RUNNING, and the same
   idempotency key returns RUNNING for as long as it is asked (the handler never runs again);
 - a request that was interrupted inside its handler stays RUNNING with nothing changed, and only a
   new key makes it run;
 - the operation row records no process that owned the request, so nothing can tell "owner gone"
   from "owner slow" (rows that exist without such information are exactly these rows);
 - the domain change and the terminal result are two separate commits for this command;
 - the repeat of an applied change under a new key is refused by the revision guard, so nothing is
   applied twice.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    request,
    value,
)
from tests.integration.test_workspace_runtime_exclusivity_baseline import (
    _child_env,  # pyright: ignore[reportPrivateUsage]
)

from thoth.adapters.storage.workspace_owner import WorkspaceInUse
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.operation import OperationRecord
from thoth.protocol.jsonrpc import JsonRpcRequest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "project:stale-running"
_OWNER_PROCESS_COLUMNS = {
    "owner_worker_id",
    "owner_process_id",
    "owner_process_marker",
    "owner_host_token",
}

_CHILD = r"""
import asyncio, os, sys, time
from pathlib import Path

from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.apps.runtime import create_runtime
from thoth.application.commands.projects import ProjectCommandHandlers
from thoth.protocol.jsonrpc import JsonRpcRequest


def build(method, key, values):
    return JsonRpcRequest.model_validate({
        "id": key, "method": method,
        "params": {"_meta": {"idempotencyKey": key}, "input": values},
    })


async def main(workspace, mode, marker):
    runtime = create_runtime(workspace)
    created = await runtime.bus.dispatch(build("project/create", "create", {
        "project_id": "project:stale-running", "name": "Original",
        "cutoff_at": "2026-09-05T00:00:00Z"}))
    assert created.error is None, created.error
    update = build("project/metadata/update", "rename", {
        "project_id": "project:stale-running", "expected_revision": 0, "name": "Renamed"})
    if mode == "after-domain-commit":
        def die(*_args, **_kwargs):
            Path(marker).write_text(str(os.getpid()), encoding="utf-8")
            os._exit(37)
        SqliteOperationStore.complete = die
    else:
        def stuck(self, *_args, **_kwargs):
            Path(marker).write_text(str(os.getpid()), encoding="utf-8")
            time.sleep(600)
        ProjectCommandHandlers._update = stuck
    await runtime.bus.dispatch(update)
    raise SystemExit("the interruption point was not reached")


asyncio.run(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
"""


def _interrupted_child(tmp_path: Path, workspace: Path, mode: str) -> int | None:
    marker = tmp_path / f"{mode}.marker"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    with (tmp_path / f"{mode}.log").open("wb") as sink:
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", _CHILD, str(workspace), mode, str(marker)],
            cwd=ROOT,
            env=_child_env(home),
            stdout=sink,
            stderr=sink,
        )
    deadline = time.monotonic() + 120
    while not marker.is_file() and time.monotonic() < deadline:
        assert child.poll() is None, (tmp_path / f"{mode}.log").read_text(errors="replace")[-2000:]
        time.sleep(0.05)
    assert marker.is_file(), "the child never reached the interruption point"
    if mode == "after-domain-commit":
        return child.wait(timeout=60)  # the child ends itself
    child.kill()  # a forced kill while the handler is still running
    child.wait(timeout=60)
    return None


def _open(workspace: Path) -> AppRuntime:
    """Windows frees a killed process's workspace lock a few milliseconds after it is gone."""
    deadline = time.monotonic() + 10
    while True:
        try:
            return create_runtime(workspace)
        except WorkspaceInUse:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def _rows(runtime: AppRuntime, method: str) -> list[tuple[str, str, str | None]]:
    with runtime.ledger.engine.connect() as connection:
        found = connection.exec_driver_sql(
            "SELECT operation_id, state, completed_at FROM operations WHERE method = ? "
            "ORDER BY created_at",
            (method,),
        ).all()
    return [(str(a), str(b), None if c is None else str(c)) for a, b, c in found]


def _rename(key: str, expected_revision: int) -> JsonRpcRequest:
    return request(
        "project/metadata/update",
        key,
        {"project_id": PROJECT, "expected_revision": expected_revision, "name": "Renamed"},
    )


async def test_a_request_interrupted_after_its_change_was_committed_stays_running(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    assert _interrupted_child(tmp_path, workspace, "after-domain-commit") == 37
    runtime = _open(workspace)
    try:
        # The change was committed; the terminal result never was.
        assert [row[1:] for row in _rows(runtime, "project/metadata/update")] == [("RUNNING", None)]
        read = value(
            await runtime.bus.query(request("project/read", "after", {"project_id": PROJECT}))
        )
        assert read["name"] == "Renamed" and read["revision"] == 1

        # The same key is answered with RUNNING every time; the handler never runs again.
        first = await runtime.bus.dispatch(_rename("rename", 0))
        again = await runtime.bus.dispatch(_rename("rename", 0))
        for replay in (first, again):
            assert replay.error is None and replay.result is not None
            assert replay.result["state"] == "RUNNING"
            assert replay.result["value"] is None
            assert replay.result["execution_observation"] == "UNKNOWN"
        assert [row[1] for row in _rows(runtime, "project/metadata/update")] == ["RUNNING"]
        after_replays = domain_snapshot(runtime.ledger.engine)

        # The repeat of the applied change under a NEW key is refused by the revision guard:
        # nothing is applied twice, and the refusal changes no domain row.
        repeat = await runtime.bus.dispatch(_rename("rename-again", 0))
        assert repeat.error is not None
        assert "expected_revision" in repeat.error.message
        assert domain_snapshot(runtime.ledger.engine) == after_replays
        assert [row[1] for row in _rows(runtime, "project/metadata/update")] == [
            "RUNNING",
            "FAILED",
        ]
    finally:
        runtime.close()


async def test_a_request_interrupted_inside_its_handler_stays_running_with_nothing_changed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    assert _interrupted_child(tmp_path, workspace, "inside-handler") is None
    runtime = _open(workspace)
    try:
        assert [row[1:] for row in _rows(runtime, "project/metadata/update")] == [("RUNNING", None)]
        before = value(
            await runtime.bus.query(request("project/read", "before", {"project_id": PROJECT}))
        )
        assert before["name"] == "Original" and before["revision"] == 0

        # Asking again with the same key neither runs the change nor closes the request.
        for _ in range(2):
            replay = await runtime.bus.dispatch(_rename("rename", 0))
            assert replay.error is None and replay.result is not None
            assert replay.result["state"] == "RUNNING" and replay.result["value"] is None
        unchanged = value(
            await runtime.bus.query(request("project/read", "unchanged", {"project_id": PROJECT}))
        )
        assert unchanged["name"] == "Original" and unchanged["revision"] == 0

        # Only a new key runs it, once.
        fresh = value(await runtime.bus.dispatch(_rename("rename-new", 0)))
        assert fresh["name"] == "Renamed" and fresh["revision"] == 1
        assert [row[1] for row in _rows(runtime, "project/metadata/update")] == [
            "RUNNING",
            "SUCCEEDED",
        ]
    finally:
        runtime.close()


async def test_the_operation_row_records_no_owning_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    assert _interrupted_child(tmp_path, workspace, "after-domain-commit") == 37
    runtime = _open(workspace)
    try:
        with runtime.ledger.engine.connect() as connection:
            columns = {
                str(row[1])
                for row in connection.exec_driver_sql("PRAGMA table_info(operations)").all()
            }
            stuck = (
                connection.exec_driver_sql(
                    "SELECT * FROM operations WHERE method = 'project/metadata/update'"
                )
                .mappings()
                .one()
            )
        # Only the authenticated actor is recorded, never the process that was running the work, so
        # a RUNNING row cannot say whether its owner is gone. D-B adds those columns on purpose.
        assert not _OWNER_PROCESS_COLUMNS & columns
        assert not _OWNER_PROCESS_COLUMNS & set(OperationRecord.model_fields)
        assert stuck["state"] == "RUNNING"
        assert json.dumps(dict(stuck), default=str).count(str(workspace)) == 0
    finally:
        runtime.close()


async def test_the_change_and_the_terminal_result_are_two_separate_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process stand-in for the kill above: the terminal write fails after the change."""
    from thoth.adapters.storage.operations import SqliteOperationStore

    class Died(BaseException):
        pass

    def die(*_args: object, **_kwargs: object) -> None:
        raise Died

    runtime = create_runtime(tmp_path / "workspace")
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": PROJECT,
                        "name": "Original",
                        "cutoff_at": "2026-09-05T00:00:00Z",
                    },
                )
            )
        )
        before = domain_snapshot(runtime.ledger.engine)
        monkeypatch.setattr(SqliteOperationStore, "complete", die)
        with pytest.raises(Died):
            await runtime.bus.dispatch(_rename("rename", 0))
        monkeypatch.undo()
        # The change is already in the domain rows, while the operation is still RUNNING: the two
        # are separate commits, so an interruption between them leaves exactly this state.
        assert domain_snapshot(runtime.ledger.engine) != before
        read = value(
            await runtime.bus.query(request("project/read", "after", {"project_id": PROJECT}))
        )
        assert read["name"] == "Renamed" and read["revision"] == 1
        assert [row[1] for row in _rows(runtime, "project/metadata/update")] == ["RUNNING"]
    finally:
        runtime.close()


_GUARDED_PROJECT_COMMANDS: list[tuple[str, dict[str, object]]] = [
    ("project/metadata/update", {"name": "Renamed"}),
    ("project/overlay/update", {"overlay": "interrupted-overlay"}),
]


@pytest.mark.parametrize(
    ("method", "fields"),
    _GUARDED_PROJECT_COMMANDS,
    ids=[method for method, _ in _GUARDED_PROJECT_COMMANDS],
)
async def test_an_interrupted_project_change_is_refused_when_asked_again_with_a_new_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    fields: dict[str, object],
) -> None:
    from thoth.adapters.storage.operations import SqliteOperationStore

    class Died(BaseException):
        pass

    def die(*_args: object, **_kwargs: object) -> None:
        raise Died

    runtime = create_runtime(tmp_path / "workspace")
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": PROJECT,
                        "name": "Original",
                        "cutoff_at": "2026-09-05T00:00:00Z",
                    },
                )
            )
        )
        payload: dict[str, object] = {"project_id": PROJECT, "expected_revision": 0, **fields}
        monkeypatch.setattr(SqliteOperationStore, "complete", die)
        with pytest.raises(Died):
            await runtime.bus.dispatch(request(method, "first", payload))
        monkeypatch.undo()
        applied = domain_snapshot(runtime.ledger.engine)
        repeat = await runtime.bus.dispatch(request(method, "second", payload))
        assert repeat.error is not None and "expected_revision" in repeat.error.message
        assert domain_snapshot(runtime.ledger.engine) == applied
    finally:
        runtime.close()
