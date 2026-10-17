"""M3 creation diagnostics: preserve RED evidence; exclude RED from CP3 R commits."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.dependency import SqliteDependencyGraph
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.project import WorkThread
from thoth.protocol.deferred import current_operation

_EVIDENCE = Path(__file__).resolve().parents[2] / "outputs/refactor-step68-20261014"
_THREAD = "thread:a03-storage"


def _save(name: str, data: dict[str, Any]) -> None:
    _EVIDENCE.mkdir(parents=True, exist_ok=True)
    (_EVIDENCE / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _views(runtime: AppRuntime, project: str, phase: str) -> dict[str, Any]:
    return {
        "thread": value(
            await runtime.bus.query(
                request("thread/read", "read-thread", {"project_id": project, "thread_id": _THREAD})
            )
        ),
        "objects": value(
            await runtime.bus.dispatch(
                request("object/list", "read-objects-" + phase, {"project_id": project})
            )
        ),
        "heads": runtime.ledger.read_heads(project),
        "receipts": [r.model_dump(mode="json") for r in runtime.ledger.read_receipts(project)],
        "dependencies": [
            r.model_dump(mode="json")
            for r in SqliteDependencyGraph(runtime.ledger.engine).read_relations(project)
        ],
    }


async def test_creation_success_control_and_replay(tmp_path: Path) -> None:
    runtime, ctx = await setup(tmp_path)
    project = ctx["project"]
    try:
        parent = value(
            await runtime.bus.dispatch(
                request(
                    "object/materialize",
                    "control-materialize",
                    {
                        "project_id": project,
                        "thread_id": _THREAD,
                        "purpose_statement": "An independent M3 creation control",
                        "focus_refs": ctx["spans"],
                        "trigger_evidence_refs": ctx["spans"],
                        "profile_refs": ["GENERAL_RND_DECISION"],
                    },
                )
            )
        )["object"]
        command = request(
            "object/followup/create",
            "control-followup",
            {
                "project_id": project,
                "object_id": parent["object_id"],
                "expected_revision_digest": parent["revision_digest"],
                "purpose_statement": "Follow the M3 control evidence",
                "trigger_refs": ctx["spans"],
                "inherit_scope": True,
            },
        )
        created = value(await runtime.bus.dispatch(command))
        child = created["object"]
        assert child["parent_object_id"] == parent["object_id"]
        assert created["lineage"]["relation_type"] == "FOLLOWUP_OF"
        assert created["lineage"]["target_ref"] == "OBJECT:" + parent["object_id"]
        before_replay = snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(command)) == created
        assert snapshot(runtime.ledger.engine) == before_replay
        conflicting = request(
            command.method,
            "control-followup",
            {**dict(command.params.input), "purpose_statement": "Conflicting content"},
        )
        assert (await runtime.bus.dispatch(conflicting)).error is not None
        assert snapshot(runtime.ledger.engine) == before_replay
        live = await _views(runtime, project, "live")
        after_read = snapshot(runtime.ledger.engine)
        assert parent["object_id"] in live["thread"]["current_object_ids"]
        assert child["object_id"] in live["thread"]["current_object_ids"]
        assert live["heads"]["DECISION_OBJECT:" + child["object_id"]] == child["revision_digest"]
        assert child["object_revision_id"] in created["commit"]["receipt"]["subject_refs"]
        assert any(
            r["source_ref"] == "OBJECT:" + child["object_id"]
            and r["target_ref"] == "OBJECT:" + parent["object_id"]
            for r in live["dependencies"]
        )
        _save("control-live.json", {"created": created, "views": live})
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == after_read
        restored = await _views(reopened, project, "reopened")
        assert restored == live
        _save("control-reopened.json", {"views": restored, "snapshot_unchanged": True})
    finally:
        reopened.close()


@pytest.mark.parametrize("fault", ["thread_cas_reject", "after_thread_write"])
async def test_followup_failure_keeps_creation_checkpoint_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Apply the owner/accepted-command contract to followup as an explicit interpretation."""
    runtime, ctx = await setup(tmp_path)
    project = ctx["project"]
    key = "m3-followup-" + fault
    hits: list[dict[str, Any]] = []
    original = SqliteThreadStore.update
    try:
        parent = value(
            await runtime.bus.dispatch(
                request(
                    "object/read", "parent", {"project_id": project, "object_id": ctx["object"]}
                )
            )
        )["object"]
        command = request(
            "object/followup/create",
            key,
            {
                "project_id": project,
                "object_id": ctx["object"],
                "expected_revision_digest": parent["revision_digest"],
                "purpose_statement": "M3 atomic follow-up diagnostic",
                "trigger_refs": ctx["spans"],
                "inherit_scope": True,
            },
        )
        before = snapshot(runtime.ledger.engine)

        def injected(
            store: SqliteThreadStore, thread: WorkThread, *, expected_revision: int
        ) -> bool:
            operation = current_operation.get()
            if (
                operation is None
                or operation.idempotency_key != key
                or operation.project_id != project
                or thread.thread_id != _THREAD
            ):
                return original(store, thread, expected_revision=expected_revision)
            hit: dict[str, Any] = {
                "operation_id": operation.operation_id,
                "method": operation.method,
                "key": operation.idempotency_key,
                "thread_id": thread.thread_id,
                "proposed_object_ids": list(thread.current_object_ids),
                "expected_revision": expected_revision,
                "fault": fault,
            }
            hits.append(hit)
            if fault == "thread_cas_reject":
                hit["original_called"] = False
                return False
            hit["original_called"] = True
            hit["original_result"] = original(store, thread, expected_revision=expected_revision)
            raise RuntimeError("M3 failure after followup thread attachment")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteThreadStore, "update", injected)
            response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
        operation_rows = [
            json.loads(row)
            for row in after["operations"]
            if json.loads(row)["idempotency_key"] == key
        ]
        delta = {}
        for table in before.keys() | after.keys():
            old, new = Counter(before.get(table, ())), Counter(after.get(table, ()))
            if old != new:
                delta[table] = {
                    "added": [json.loads(row) for row in (new - old).elements()],
                    "removed": [json.loads(row) for row in (old - new).elements()],
                }
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        restored = await _views(reopened, project, "fault-reopened")
        _save(
            fault + ".json",
            {
                "contract_status": "followup interpretation of accepted creation owner contract",
                "request": command.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "seam_hits": hits,
                "operations": operation_rows,
                "whole_db_before": before,
                "whole_db_after": after,
                "whole_db_delta": delta,
                "whole_db_reopened": restarted,
                "reopened": restored,
            },
        )
        assert len(hits) == 1, hits
        assert restarted == after
        assert response.error is not None, "Rejected thread attachment must not report success"
        assert len(operation_rows) == 1 and operation_rows[0]["state"] == "FAILED"
        allowed = failed_command_allowances(
            reopened.ledger.engine,
            command,
        )
        assert_phase_delta(before, after, allowed)
    finally:
        reopened.close()
