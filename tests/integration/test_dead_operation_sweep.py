"""A killed process leaves its operations RUNNING; only a permanent delete seals them.

Owners are real: a child process records its own worker identity and exits (or is killed), so
"gone" is what the operating system says, never a stand-in. No model, login or network is used.
Opening a workspace seals nothing: an operation whose outcome is unknown stays RUNNING and a
measured command is sealed only when its own key is sent again (the owner-recovery tests).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_stale_running_current_behavior import (
    _interrupted_child,  # pyright: ignore[reportPrivateUsage]
    _open,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.test_workspace_runtime_exclusivity_baseline import (
    _child_env,  # pyright: ignore[reportPrivateUsage]
)

from thoth.adapters.operation_host_identity import operation_host_token
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.worker_identity import LocalWorkerIdentity
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationOwnerRecord, OperationRecord

PROJECT = "project:sweep"
NAME = "Sweep 시험"
HOST = operation_host_token(DeploymentMode.LOCAL)
pytestmark = pytest.mark.skipif(HOST is None, reason="no host identity on this platform")

_WHO = (
    "import json; from thoth.adapters.worker_identity import LocalWorkerIdentity as W; "
    "w = W(); print(json.dumps([w.worker_id, w.process_id, w.process_marker]))"
)


def _dead_worker(tmp_path: Path) -> tuple[str, int, str]:
    """A worker identity of a process that has already ended."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    done = subprocess.run(
        [sys.executable, "-B", "-c", _WHO],
        env=_child_env(home),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    worker_id, pid, marker = json.loads(done.stdout.strip().splitlines()[-1])
    return str(worker_id), int(pid), str(marker)


def _owner(worker: tuple[str, int, str], *, host: str | None = HOST) -> OperationOwnerRecord:
    return OperationOwnerRecord(
        worker_id=worker[0], process_id=worker[1], process_marker=worker[2], host_token=host
    )


def _claim(
    runtime: AppRuntime,
    key: str,
    method: str,
    owner: OperationOwnerRecord | None,
    project: str = PROJECT,
) -> str:
    candidate = OperationRecord(
        operation_id=f"operation:{key}",
        project_id=project,
        method=method,
        idempotency_key=key,
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=datetime.now(UTC),
        owner=owner,
    )
    SqliteOperationStore(runtime.ledger.engine).claim(candidate)
    return candidate.operation_id


def _states(runtime: AppRuntime) -> dict[str, str]:
    with runtime.ledger.engine.connect() as connection:
        found = connection.exec_driver_sql("SELECT idempotency_key, state FROM operations").all()
    return {str(key): str(state) for key, state in found}


def _fingerprint(runtime: AppRuntime) -> tuple[Any, ...]:
    with runtime.ledger.engine.connect() as connection:
        parts = [
            connection.exec_driver_sql(f"SELECT * FROM {table} ORDER BY 1, 2").all()
            for table in ("operations", "events", "operation_resource_bindings")
        ]
    return tuple(tuple(tuple(row) for row in part) for part in parts)


async def _create(runtime: AppRuntime) -> None:
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "create",
                {"project_id": PROJECT, "name": NAME, "cutoff_at": "2026-09-05T00:00:00Z"},
            )
        )
    )


async def _archive(runtime: AppRuntime, project: str = PROJECT) -> None:
    current = value(
        await runtime.bus.query(request("project/read", "read", {"project_id": project}))
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/archive",
                "archive",
                {"project_id": project, "expected_revision": current["revision"]},
            )
        )
    )


async def _delete(
    runtime: AppRuntime, key: str = "delete", project: str = PROJECT, name: str = NAME
) -> Any:
    return await runtime.bus.dispatch(
        request("project/permanentDelete", key, {"project_id": project, "confirm_name": name})
    )


async def test_opening_a_workspace_seals_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    dead = _dead_worker(tmp_path)
    first = create_runtime(workspace)
    try:
        await _create(first)
        for key, method in (
            ("measured", "project/metadata/update"),
            ("unmeasured", "project/source/connect"),
            ("restore", "revision/restore/apply"),
            ("research", "thread/start"),
        ):
            _claim(first, key, method, _owner(dead))
    finally:
        first.close()
    second = _open(workspace)
    try:
        states = _states(second)
        assert [states[k] for k in ("measured", "unmeasured", "restore", "research")] == [
            "RUNNING"
        ] * 4
        settled = _fingerprint(second)
    finally:
        second.close()
    third = _open(workspace)
    try:
        assert _fingerprint(third) == settled
    finally:
        third.close()


async def test_a_killed_childs_leftover_stays_running_but_does_not_block_a_delete(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    assert _interrupted_child(tmp_path, workspace, "excluded") is None
    runtime = _open(workspace)
    project, name = "project:stale-running", "Original"
    try:
        assert [row[1] for row in _rows(runtime, "project/source/connect")] == ["RUNNING"]
        await _archive(runtime, project)
        response = await _delete(runtime, project=project, name=name)
        assert response.error is None, response.error
        assert value(response)["deleted"] is True
        assert _rows(runtime, "project/source/connect") == []
    finally:
        runtime.close()


async def test_delete_seals_a_dead_research_operation_and_then_succeeds(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    dead = _dead_worker(tmp_path)
    runtime = create_runtime(workspace)
    try:
        await _create(runtime)
        await _archive(runtime)
        _claim(runtime, "research", "thread/start", _owner(dead))
        _claim(runtime, "plain", "project/metadata/update", _owner(dead))
        response = await _delete(runtime)
        assert response.error is None, response.error
        assert value(response)["deleted"] is True
        remaining = _states(runtime)
        assert "research" not in remaining and "plain" not in remaining
    finally:
        runtime.close()


async def test_delete_is_still_refused_for_a_living_or_unprovable_owner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    dead = _dead_worker(tmp_path)
    alive = LocalWorkerIdentity()
    runtime = create_runtime(workspace)
    try:
        await _create(runtime)
        await _archive(runtime)
        _claim(runtime, "dead", "thread/start", _owner(dead))
        _claim(
            runtime,
            "alive",
            "project/role/assign",
            _owner((alive.worker_id, alive.process_id, alive.process_marker)),
        )
        refused = await _delete(runtime, "d1")
        assert refused.error is not None
        assert refused.error.data["reason_code"] == "PROJECT_HAS_ACTIVE_OPERATION"
        # The dead one stays sealed after the refusal; the living one is untouched.
        assert _states(runtime)["dead"] == "FAILED" and _states(runtime)["alive"] == "RUNNING"

        alive.close()  # now that worker is gone too, but only the stored record could say so
        _claim(runtime, "unprovable", "thread/input", None)
        again = await _delete(runtime, "d2")
        assert again.error is not None
        assert again.error.data["reason_code"] == "PROJECT_HAS_ACTIVE_OPERATION"
        assert _states(runtime)["unprovable"] == "RUNNING"
    finally:
        runtime.close()


async def test_a_project_that_is_not_archived_or_misnamed_is_not_touched_by_the_seal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    dead = _dead_worker(tmp_path)
    runtime = create_runtime(workspace)
    try:
        await _create(runtime)
        _claim(runtime, "dead", "thread/start", _owner(dead))
        not_archived = await _delete(runtime, "d1")
        assert not_archived.error is not None
        assert not_archived.error.data["reason_code"] == "PROJECT_NOT_ARCHIVED"
        await _archive(runtime)
        wrong = await _delete(runtime, "d2", name="x")
        assert wrong.error is not None
        assert _states(runtime)["dead"] == "RUNNING"
    finally:
        runtime.close()


def _cannot_tell(*_args: object, **_kw: object) -> None:
    return None


async def test_an_owner_whose_state_cannot_be_read_is_left_alone_before_a_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_alive answers None when the system will not say; only an exact False seals."""
    workspace = tmp_path / "workspace"
    dead = _dead_worker(tmp_path)
    runtime = create_runtime(workspace)
    try:
        await _create(runtime)
        await _archive(runtime)
        _claim(runtime, "plain", "project/metadata/update", _owner(dead))
        _claim(runtime, "research", "thread/start", _owner(dead))
        monkeypatch.setattr(LocalWorkerIdentity, "is_alive", _cannot_tell)
        refused = await _delete(runtime)
        assert refused.error is not None
        assert refused.error.data["reason_code"] == "PROJECT_HAS_ACTIVE_OPERATION"
        assert _states(runtime)["plain"] == "RUNNING" and _states(runtime)["research"] == "RUNNING"
    finally:
        runtime.close()
