from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock, get_ident

import pytest
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    fail_after,
    prepare_project,
    request,
    value,
)

from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.investigation import SqliteInvestigationStore
from thoth.adapters.storage.thread_runtime import SqliteThreadRuntimeStore
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.protocol.jsonrpc import JsonRpcResponse


async def test_independent_runtime_object_cas_has_one_current_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first, project = await prepare_project(workspace)
    second = create_runtime(workspace)
    try:
        started = value(
            await first.bus.dispatch(
                request(
                    "thread/start",
                    "start-race",
                    {
                        "project_id": project,
                        "thread_id": "thread:cas",
                        "problem": "Compare measurements",
                    },
                )
            )
        )
        object_id = str(started["current_object_ids"][0])
        current = value(
            await first.bus.dispatch(
                request(
                    "object/read",
                    "read-race",
                    {
                        "project_id": project,
                        "object_id": object_id,
                    },
                )
            )
        )["object"]
        barrier, lock = Barrier(2), Lock()
        seen: set[int] = set()
        original = SqliteDecisionObjectStore.read_object

        def synchronized_read(
            store: SqliteDecisionObjectStore,
            project_id: str,
            target: str,
            revision_digest: str | None,
        ) -> DecisionObjectRecord | None:
            result = original(store, project_id, target, revision_digest)
            wait = False
            with lock:
                if target == object_id and get_ident() not in seen:
                    seen.add(get_ident())
                    wait = True
            if wait:
                barrier.wait(timeout=10)
            return result

        def dispatch(runtime: AppRuntime, key: str) -> JsonRpcResponse:
            return asyncio.run(
                runtime.bus.dispatch(
                    request(
                        "object/frame/revise",
                        key,
                        {
                            "project_id": project,
                            "object_id": object_id,
                            "expected_revision_digest": current["revision_digest"],
                            "frame_patch": {"purpose_statement": "Updated measurement " + key},
                            "evidence_refs": [],
                            "reason": "Concurrent explicit correction",
                        },
                    )
                )
            )

        with monkeypatch.context() as patch:
            patch.setattr(SqliteDecisionObjectStore, "read_object", synchronized_read)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(dispatch, first, "first"),
                    pool.submit(dispatch, second, "second"),
                ]
                results = [future.result(timeout=20) for future in futures]
        assert sum(result.error is None for result in results) == 1
        assert len(first.ledger.read_revisions(project, "DECISION_OBJECT", object_id)) == 2
        latest = SqliteDecisionObjectStore(first.ledger.engine).read_object(
            project, object_id, None
        )
        assert latest is not None
        assert (
            first.ledger.read_heads(project)["DECISION_OBJECT:" + object_id]
            == latest.revision_digest
        )
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    "method",
    [
        "thread/pause",
        "thread/resume",
        "thread/stop",
        "thread/steer",
        "thread/fork",
        "thread/metadata/update",
    ],
)
async def test_thread_control_fault_rolls_back_state_activity_and_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    workspace = tmp_path / "workspace"
    runtime, project = await prepare_project(workspace)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": project,
                        "thread_id": "thread:controls",
                        "problem": "Review evidence",
                    },
                )
            )
        )
        if method == "thread/resume":
            value(
                await runtime.bus.dispatch(
                    request(
                        "thread/pause",
                        "pause",
                        {
                            "project_id": project,
                            "thread_id": "thread:controls",
                        },
                    )
                )
            )
        before = domain_snapshot(runtime.ledger.engine)
        payload: dict[str, object] = {"project_id": project, "thread_id": "thread:controls"}
        if method == "thread/steer":
            payload["instruction"] = "Check the conflicting measurement"
        if method == "thread/fork":
            payload["child_thread_id"] = "thread:child"
        if method == "thread/metadata/update":
            payload.update({"display_name": "Revised title", "expected_revision": 0})
        with monkeypatch.context() as patch:
            fail_after(patch, SqliteThreadRuntimeStore, "append_activity")
            failed = await runtime.bus.dispatch(request(method, "control-fault", payload))
            assert failed.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("owner", "method"),
    [
        (SqliteDecisionObjectStore, "add_object"),
        (SqliteDecisionObjectStore, "append_audit"),
        (SqliteInvestigationStore, "append_audit"),
    ],
)
async def test_thread_start_fault_has_no_half_object_or_head_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: type[object],
    method: str,
) -> None:
    workspace = tmp_path / "workspace"
    runtime, project = await prepare_project(workspace)
    before = domain_snapshot(runtime.ledger.engine)
    try:
        with monkeypatch.context() as patch:
            fail_after(patch, owner, method)
            failed = await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start-fault",
                    {
                        "project_id": project,
                        "thread_id": "thread:atomic-start",
                        "problem": "Explain this experiment using the current evidence",
                    },
                )
            )
            assert failed.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
    finally:
        reopened.close()


async def test_thread_start_success_duplicate_and_restart_keep_same_object(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime, project = await prepare_project(workspace)
    start = request(
        "thread/start",
        "start-success",
        {
            "project_id": project,
            "thread_id": "thread:atomic-start",
            "problem": "Explain the failed experiment",
        },
    )
    try:
        started = value(await runtime.bus.dispatch(start))
        after = domain_snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(start)) == started
        assert domain_snapshot(runtime.ledger.engine) == after
        duplicate = await runtime.bus.dispatch(
            request(
                "thread/start",
                "other-key",
                {
                    "project_id": project,
                    "thread_id": "thread:atomic-start",
                    "problem": "Explain the failed experiment",
                },
            )
        )
        assert duplicate.error is not None
        assert domain_snapshot(runtime.ledger.engine) == after
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        restored = value(
            await reopened.bus.dispatch(
                request(
                    "thread/read",
                    "read-restored",
                    {
                        "project_id": project,
                        "thread_id": "thread:atomic-start",
                    },
                )
            )
        )
        assert restored == started
        assert domain_snapshot(reopened.ledger.engine) == after
    finally:
        reopened.close()


async def test_source_disconnect_cas_failure_rolls_back_binding_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thoth.adapters.storage.projects import SqliteProjectStore
    from thoth.domain.project import Project

    workspace = tmp_path / "workspace"
    runtime, project = await prepare_project(workspace)
    inbox = workspace / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "source.md").write_text("# Evidence\n\nA source-bound measurement.\n")
    try:
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": project,
                        "relative_path": "source.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        before = domain_snapshot(runtime.ledger.engine)

        def conflict(store: SqliteProjectStore, record: Project, *, expected_revision: int) -> bool:
            del store, record, expected_revision
            return False

        with monkeypatch.context() as patch:
            patch.setattr(SqliteProjectStore, "update", conflict)
            failed = await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "disconnect",
                    {
                        "project_id": project,
                        "binding_id": connected["binding"]["binding_id"],
                        "mode": "REVOKE",
                    },
                )
            )
            assert failed.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
    finally:
        reopened.close()
