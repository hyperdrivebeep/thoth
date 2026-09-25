from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.domain.project import WorkThread


@pytest.mark.parametrize("fault", ["after_thread_write", "thread_cas_reject"])
async def test_public_materialization_and_thread_attachment_are_one_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    runtime, ctx = await setup(tmp_path)
    command = request(
        "object/materialize",
        "object-fault",
        {
            "project_id": ctx["project"],
            "thread_id": "thread:a03-storage",
            "purpose_statement": "A distinct explicit follow-up investigation",
            "focus_refs": ctx["spans"],
            "trigger_evidence_refs": ctx["spans"],
            "profile_refs": ["GENERAL_RND_DECISION"],
        },
    )
    before = snapshot(runtime.ledger.engine)
    original = SqliteThreadStore.update
    calls = 0

    def fail(store: SqliteThreadStore, thread: WorkThread, *, expected_revision: int) -> bool:
        nonlocal calls
        calls += 1
        if fault == "thread_cas_reject":
            return False
        original(store, thread, expected_revision=expected_revision)
        raise RuntimeError("thread attachment fault")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteThreadStore, "update", fail)
            response = await runtime.bus.dispatch(command)
        assert calls == 1 and response.error is not None
        after = snapshot(runtime.ledger.engine)
        for table in (
            "semantic_revisions",
            "working_heads",
            "decision_objects",
            "object_candidates",
            "threads",
        ):
            assert after[table] == before[table], table
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert_phase_delta(before, after, allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        created = value(
            await reopened.bus.dispatch(
                request("object/materialize", "retry", dict(command.params.input))
            )
        )["object"]
        current = value(
            await reopened.bus.query(
                request(
                    "thread/read",
                    "thread",
                    {"project_id": ctx["project"], "thread_id": "thread:a03-storage"},
                )
            )
        )
        assert created["object_id"] in current["current_object_ids"]
    finally:
        reopened.close()


async def test_entry_hold_candidate_remains_valid_without_inventing_an_object(
    tmp_path: Path,
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        before = snapshot(runtime.ledger.engine)
        held = value(
            await runtime.bus.dispatch(
                request(
                    "object/materialize",
                    "hold",
                    {
                        "project_id": ctx["project"],
                        "thread_id": "thread:a03-storage",
                        "purpose_statement": "A distinct but incomplete frame",
                        "focus_refs": [],
                        "trigger_evidence_refs": [],
                        "profile_refs": ["GENERAL_RND_DECISION"],
                    },
                )
            )
        )
        assert held["object"] is None and held["commit"] is None
        assert held["candidate"]["permitted_transition"] == "HOLD_ENTRY_INVALID"
        after = snapshot(runtime.ledger.engine)
        assert len(after["object_candidates"]) == len(before["object_candidates"]) + 1
        assert after["semantic_revisions"] == before["semantic_revisions"]
        assert after["threads"] == before["threads"]
    finally:
        runtime.close()
