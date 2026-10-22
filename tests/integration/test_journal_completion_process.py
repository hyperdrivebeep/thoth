"""Run only after the concurrent FULL has ended: real process termination boundaries."""

import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_operation_journal_failure_boundaries import NoModels
from tests.integration.test_workspace_runtime_exclusivity_baseline import (
    _child_env,  # pyright: ignore[reportPrivateUsage]
)

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.workspace_owner import WorkspaceInUse
from thoth.apps.runtime import AppRuntime

ROOT = Path(__file__).resolve().parents[2]
CHILD = r"""
import asyncio, sys, time
from pathlib import Path
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request,value
from tests.integration.test_operation_journal_failure_boundaries import NoModels
from thoth.adapters.storage.events import SqliteEventStore

async def main(workspace, marker, phase):
    model=NoModels()
    runtime=create_runtime(workspace,model_resolver=model)
    value(await runtime.bus.dispatch(request('project/create','create',{
        'project_id':'project:j-process','name':'Original','cutoff_at':'2026-10-01T00:00:00Z'})))
    command=request('project/metadata/update','gap',{
        'project_id':'project:j-process','expected_revision':0,'name':'Saved once'})
    ticket=runtime.bus.claim(command)
    identifier=ticket.operation.operation_id
    point_insert=SqliteEventStore.insert_checkpoint
    event_insert=SqliteEventStore.insert_event
    def ready():
        assert model.calls==0
        marker.write_text('ready',encoding='utf-8')
        time.sleep(120)
        raise AssertionError('parent did not terminate the test child')
    if phase=='repair-checkpoint':
        def initial_gap(self,point):
            if point.operation_id==identifier:raise RuntimeError('initial gap')
            point_insert(self,point)
        SqliteEventStore.insert_checkpoint=initial_gap
        try:await runtime.bus.execute(ticket)
        except RuntimeError as exc:assert str(exc)=='initial gap'
        else:raise AssertionError('initial gap not reached')
    def checkpoint(self,point):
        if point.operation_id==identifier and phase=='normal-checkpoint':ready()
        point_insert(self,point)
        if point.operation_id==identifier and phase=='repair-checkpoint':ready()
    def event(self,item):
        event_insert(self,item)
        if (item.operation_id==identifier
                and item.event_type=='project/metadata/updated'
                and phase=='normal-notification'):
            ready()
    SqliteEventStore.insert_checkpoint=checkpoint
    SqliteEventStore.insert_event=event
    if phase=='repair-checkpoint':await runtime.bus.dispatch(command)
    else:await runtime.bus.execute(ticket)
    raise AssertionError('termination point not reached')
asyncio.run(main(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]))
"""


def _open(workspace: Path, model: NoModels) -> AppRuntime:
    """Use the existing bounded Windows lock-release retry from the owner recovery tests."""
    deadline = time.monotonic() + 10
    while True:
        try:
            return create_runtime(workspace, model_resolver=model)
        except WorkspaceInUse:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


@pytest.mark.parametrize("phase", ["normal-checkpoint", "normal-notification", "repair-checkpoint"])
async def test_process_exit_preserves_result_and_atomic_supplement(
    tmp_path: Path, phase: str
) -> None:
    workspace, marker, home = tmp_path / "workspace", tmp_path / "ready", tmp_path / "home"
    home.mkdir()
    log = tmp_path / "child.log"
    with log.open("wb") as sink:
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", CHILD, str(workspace), str(marker), phase],
            cwd=ROOT,
            env=_child_env(home),
            stdout=sink,
            stderr=sink,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    try:
        deadline = time.monotonic() + 60
        while not marker.exists() and time.monotonic() < deadline:
            assert child.poll() is None, log.read_text(errors="replace")[-2000:]
            time.sleep(0.025)
        assert marker.exists(), "child did not reach the requested journal boundary"
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    model = NoModels()
    runtime = _open(workspace, model)
    try:
        with runtime.ledger.engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT operation_id,state FROM operations WHERE idempotency_key='gap'"
            ).one()
        identifier = str(row[0])
        assert row[1] == "SUCCEEDED"
        journal = SqliteEventStore(runtime.ledger.engine)
        assert len(journal.list_checkpoints(identifier)) == (
            1 if phase == "normal-notification" else 0
        )
        assert len(journal.list_events(identifier)) == (2 if phase == "normal-notification" else 1)
        before = snapshot(runtime.ledger.engine)
        command = request(
            "project/metadata/update",
            "gap",
            {
                "project_id": "project:j-process",
                "expected_revision": 0,
                "name": "Saved once",
            },
        )
        assert value(await runtime.bus.dispatch(command))["revision"] == 1
        after = snapshot(runtime.ledger.engine)
        assert all(before[t] == after[t] for t in before if t not in {"events", "checkpoints"})
        assert len(journal.list_events(identifier)) == 3
        assert len(journal.list_checkpoints(identifier)) == 1
        assert value(await runtime.bus.dispatch(command))["revision"] == 1
        assert snapshot(runtime.ledger.engine) == after and model.calls == 0
    finally:
        runtime.close()
