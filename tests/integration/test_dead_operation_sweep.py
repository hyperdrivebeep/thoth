"""Operations left RUNNING by a process that is gone: sealed at start and just before a delete.

Owners are real: a child process records its own worker identity and exits (or is killed), so
"gone" is what the operating system says, never a stand-in. No model, login or network is used.
"""

from __future__ import annotations

import asyncio
import json
import logging
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


async def _archive(runtime: AppRuntime) -> None:
    current = value(
        await runtime.bus.query(request("project/read", "read", {"project_id": PROJECT}))
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/archive",
                "archive",
                {"project_id": PROJECT, "expected_revision": current["revision"]},
            )
        )
    )


async def _delete(runtime: AppRuntime, key: str = "delete") -> Any:
    return await runtime.bus.dispatch(
        request("project/permanentDelete", key, {"project_id": PROJECT, "confirm_name": NAME})
    )


class _Messages(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def test_start_seals_exactly_the_non_research_operations_whose_owner_is_gone(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    dead = _dead_worker(tmp_path)
    alive = LocalWorkerIdentity()  # a live worker of this very process
    first = create_runtime(workspace)
    try:
        await _create(first)
        _claim(first, "sealable", "project/metadata/update", _owner(dead))
        _claim(first, "unmeasured", "project/source/connect", _owner(dead))
        _claim(first, "research-start", "thread/start", _owner(dead))
        _claim(first, "queued-input", "thread/input", _owner(dead))
        _claim(first, "no-owner", "project/overlay/update", None)
        _claim(first, "other-host", "project/policy/update", _owner(dead, host="b" * 64))
        _claim(first, "unknown-marker", "action/select", _owner((dead[0], dead[1], "UNKNOWN")))
        _claim(
            first,
            "alive",
            "project/role/assign",
            _owner((alive.worker_id, alive.process_id, alive.process_marker)),
        )
    finally:
        first.close()

    messages = _Messages()
    logger = logging.getLogger("thoth.operation_owner")
    logger.addHandler(messages)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        second = _open(workspace)
    finally:
        logger.removeHandler(messages)
        logger.setLevel(previous_level)
    try:
        states = _states(second)
        assert states["sealable"] == "FAILED" and states["unmeasured"] == "FAILED"
        for untouched in (
            "research-start",
            "queued-input",
            "no-owner",
            "other-host",
            "unknown-marker",
            "alive",
        ):
            assert states[untouched] == "RUNNING", untouched
        assert states["create"] == "SUCCEEDED"
        # Counts are logged: 2 sealed, 1 alive, 2 not provable, 1 without a record, 2 research.
        line = next(text for text in messages.lines if "leftover operations" in text)
        assert "sealed 2; owner alive 1; owner not provable 2; no owner record 1;" in line
        assert "research left to its own recovery 2; errors 0" in line
        # The sealed row carries the stored reason and an operation.failed event.
        with second.ledger.engine.connect() as connection:
            error = connection.exec_driver_sql(
                "SELECT error_json FROM operations WHERE idempotency_key = 'sealable'"
            ).scalar_one()
            kinds = (
                connection.exec_driver_sql(
                    "SELECT event_type FROM events WHERE operation_id = 'operation:sealable'"
                )
                .scalars()
                .all()
            )
        assert json.loads(error)["data"]["reason_code"] == "OPERATION_OWNER_GONE"
        assert list(kinds) == ["operation.failed"]
        settled = _fingerprint(second)
    finally:
        second.close()

    # Starting again changes nothing: the sealed rows are final, the others still unproven.
    third = _open(workspace)
    try:
        assert _fingerprint(third) == settled
    finally:
        third.close()
    alive.close()


async def test_a_killed_child_leaves_failed_not_running_after_the_next_start(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    assert _interrupted_child(tmp_path, workspace, "excluded") is None
    runtime = _open(workspace)
    try:
        assert [row[1] for row in _rows(runtime, "project/source/connect")] == ["FAILED"]
        with runtime.ledger.engine.connect() as connection:
            error = connection.exec_driver_sql(
                "SELECT error_json FROM operations WHERE method = 'project/source/connect'"
            ).scalar_one()
        assert json.loads(error)["data"]["reason_code"] == "OPERATION_OWNER_GONE"
        settled = _fingerprint(runtime)
    finally:
        runtime.close()
    again = _open(workspace)
    try:
        assert _fingerprint(again) == settled
    finally:
        again.close()


async def test_a_second_runtime_in_the_same_process_leaves_the_first_ones_work_running(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    first = create_runtime(workspace)
    release = asyncio.Event()
    reached = asyncio.Event()

    async def slow(_value: dict[str, Any]) -> dict[str, Any]:
        reached.set()
        await release.wait()
        return {"done": True}

    try:
        await _create(first)
        first.bus._registry.decorate("project/source/connect", lambda _h: slow)  # pyright: ignore[reportPrivateUsage]
        pending = asyncio.create_task(
            first.bus.dispatch(
                request(
                    "project/source/connect",
                    "slow",
                    {
                        "project_id": PROJECT,
                        "relative_path": "s.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                        "expected_project_revision": 0,
                    },
                )
            )
        )
        await asyncio.wait_for(reached.wait(), 60)
        second = create_runtime(workspace)  # same process: the workspace lock is shared
        try:
            assert _states(second)["slow"] == "RUNNING", "the first runtime's own work was sealed"
        finally:
            second.close()
        release.set()
        response = await asyncio.wait_for(pending, 60)
        assert response.error is None, response.error
        assert _states(first)["slow"] == "SUCCEEDED"
    finally:
        release.set()
        first.close()


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
        wrong = await runtime.bus.dispatch(
            request("project/permanentDelete", "d2", {"project_id": PROJECT, "confirm_name": "x"})
        )
        assert wrong.error is not None
        assert _states(runtime)["dead"] == "RUNNING"
    finally:
        runtime.close()


def _cannot_tell(*_args: object, **_kw: object) -> None:
    return None


async def test_an_owner_whose_state_cannot_be_read_is_left_alone_at_start_and_before_a_delete(
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
    finally:
        runtime.close()
    monkeypatch.setattr(LocalWorkerIdentity, "is_alive", _cannot_tell)
    reopened = _open(workspace)
    try:
        assert _states(reopened)["plain"] == "RUNNING"
        refused = await _delete(reopened)
        assert refused.error is not None
        assert refused.error.data["reason_code"] == "PROJECT_HAS_ACTIVE_OPERATION"
        assert (
            _states(reopened)["plain"] == "RUNNING" and _states(reopened)["research"] == "RUNNING"
        )
    finally:
        reopened.close()
