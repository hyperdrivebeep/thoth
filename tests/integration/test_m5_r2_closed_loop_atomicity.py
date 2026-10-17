"""R2 closed loop storage faults on the plain path (98-V, test only).

The normal R2 loop runs the sandbox first (an external effect, outside any commit) and then
publishes in one transaction, SandboxService.accepting_observation, which the
Outcome/Hypothesis/Action revision commit, the research projections and the artifact admission
join (T1). A failed attempt records a recovery candidate branch in its own transaction (T3). Each
target writer here runs the original first and then raises (fail_after); the whole domain snapshot
is compared at the boundary (the result artifact is staged PROHIBITED_CONTEXT before it and becomes
ELIGIBLE only inside T1), and a reopened runtime shows the same state. A real fault then shows a
same-key replay without a new sandbox call and a new-key retry with one more. The sandbox adapters
and the model are the scripted ones of test_a04/test_a05; no real model or sandbox is used. Two
shapes with separate transactions (hold then reject, failure completion then candidate) need a
prepared research test and are recorded from source in docs/verification, not injected here. This
file fixes nothing.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import domain_snapshot, fail_after
from tests.integration.test_a04_r2_closed_loop import prepare_a04, request, value
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery

from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.sandbox import SandboxExecutionState

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step98-20261015"
Snapshot = dict[str, tuple[str, ...]]
T1_FAULTS: dict[str, tuple[type[object], str]] = {
    "commit": (RevisionCommitService, "commit"),
    "persist_cycle": (ResearchIdentityService, "persist_cycle"),
    "artifact_admission": (SqliteArtifactLedger, "update_artifact_cutoff"),
}
T3_FAULTS: dict[str, tuple[type[object], str]] = {
    "receipt": (SqliteLedgerTransaction, "insert_receipt"),
    "revision": (SqliteLedgerTransaction, "insert_revision"),
}
SEMANTIC = (SandboxExecutionState.FAILED, "SEMANTIC: invalid assumption")


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v98-{name}.json").write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


def added(before: Snapshot, after: Snapshot, table: str) -> list[dict[str, Any]]:
    rows = set(after.get(table, ())) - set(before.get(table, ()))
    return [json.loads(row) for row in sorted(rows)]


def control_kinds(before: Snapshot, after: Snapshot) -> list[tuple[str, str]]:
    """Control records a request added, named by namespace and record type."""

    return sorted(
        {(row["namespace"], row["record_type"]) for row in added(before, after, "control_records")}
    )


def changed_tables(before: Snapshot, after: Snapshot) -> list[str]:
    return sorted(name for name in after if after[name] != before.get(name))


def outcome_heads(runtime: Any, project_id: str) -> list[str]:
    return sorted(
        k for k in runtime.ledger.read_heads(project_id) if k.startswith("OUTCOME:r2-outcome:")
    )


def reopened(path: Path, runtime: Any) -> None:
    other = create_runtime(path)
    try:
        assert domain_snapshot(other.ledger.engine) == domain_snapshot(runtime.ledger.engine)
    finally:
        other.close()


def loop(result: Any) -> dict[str, Any]:
    return cast(dict[str, Any], result["r2_closed_loop"])


def fault_once(
    monkeypatch: pytest.MonkeyPatch, target: str, owner: type[object], method: str
) -> dict[str, Any]:
    """Fault the first call of one R2 coordinator method, at a writer inside its boundary."""

    from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator

    original = getattr(R2ClosedLoopCoordinator, target)
    state: dict[str, Any] = {"armed": True, "hits": []}

    def wrapper(instance: Any, *args: Any, **kwargs: Any) -> Any:
        if not state["armed"]:
            return original(instance, *args, **kwargs)
        state["armed"] = False
        with monkeypatch.context() as patch:
            state["hits"] = fail_after(patch, owner, method)
            return original(instance, *args, **kwargs)

    monkeypatch.setattr(R2ClosedLoopCoordinator, target, wrapper)
    return state


def input_request(key: str, project_id: str, thread_id: str) -> Any:
    return request("thread/input", key, {"project_id": project_id, "thread_id": thread_id})


@pytest.mark.parametrize("fault", list(T1_FAULTS))
async def test_r2_publication_rolls_back_every_domain_table_after_storage_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator

    runtime, sandbox, project_id, thread_id = await prepare_a04(tmp_path, allow_sandbox=True)
    try:
        original = R2ClosedLoopCoordinator._commit_outcome  # pyright: ignore[reportPrivateUsage]
        checked: list[dict[str, Any]] = []

        def faulty(instance: Any, *args: Any, **kwargs: Any) -> Any:
            # The sandbox has already run; only the result's admission and the commit are open.
            before = domain_snapshot(runtime.ledger.engine)
            with monkeypatch.context() as patch:
                owner, method = T1_FAULTS[fault]
                hits = fail_after(patch, owner, method)
                with pytest.raises(RuntimeError, match="injected storage failure"):
                    original(instance, *args, **kwargs)
            assert hits == [T1_FAULTS[fault][1]]
            assert domain_snapshot(runtime.ledger.engine) == before
            checked.append({"fault": fault, "hits": hits})
            raise RuntimeError("verified R2 publication rollback")

        monkeypatch.setattr(R2ClosedLoopCoordinator, "_commit_outcome", faulty)
        response = await runtime.bus.dispatch(input_request("r2-fault", project_id, thread_id))
        assert response.error is not None and len(checked) == 1
        assert len(sandbox.seen_specs) == 1
        assert outcome_heads(runtime, project_id) == []
        reopened(tmp_path / "allowed", runtime)
        save(f"t1-{fault}", {"checked": checked, "sandbox_calls": len(sandbox.seen_specs)})
    finally:
        runtime.close()


async def test_r2_successful_run_publishes_once_and_names_the_rows_it_adds(
    tmp_path: Path,
) -> None:
    runtime, sandbox, project_id, thread_id = await prepare_a04(tmp_path, allow_sandbox=True)
    try:
        before = domain_snapshot(runtime.ledger.engine)
        done = value(await runtime.bus.dispatch(input_request("r2-ok", project_id, thread_id)))
        after = domain_snapshot(runtime.ledger.engine)
        assert loop(done)["terminal_state"] == "COMPLETED"
        assert len(sandbox.seen_specs) == 1
        assert len(outcome_heads(runtime, project_id)) == 1
        kinds = control_kinds(before, after)
        assert ("SANDBOX", "ADMISSION") in kinds
        assert ("BEHAVIOR_RUNTIME", "EXECUTION") in kinds
        save(
            "t1-success", {"control_kinds": kinds, "changed_tables": changed_tables(before, after)}
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", list(T3_FAULTS))
async def test_r2_recovery_candidate_commit_rolls_back_after_storage_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator

    adapter = RecoverySandboxAdapter((SEMANTIC,))
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path, "semantic", adapter, max_retries=2
    )
    try:
        original = R2ClosedLoopCoordinator._failure_execution  # pyright: ignore[reportPrivateUsage]
        checked: list[dict[str, Any]] = []

        def faulty(instance: Any, **kwargs: Any) -> Any:
            before = domain_snapshot(runtime.ledger.engine)
            with monkeypatch.context() as patch:
                owner, method = T3_FAULTS[fault]
                hits = fail_after(patch, owner, method)
                with pytest.raises(RuntimeError, match="injected storage failure"):
                    original(instance, **kwargs)
            assert hits == [T3_FAULTS[fault][1]]
            assert domain_snapshot(runtime.ledger.engine) == before
            checked.append({"fault": fault, "hits": hits})
            raise RuntimeError("verified R2 recovery rollback")

        monkeypatch.setattr(R2ClosedLoopCoordinator, "_failure_execution", faulty)
        response = await runtime.bus.dispatch(input_request("r2-t3", project_id, thread_id))
        assert response.error is not None and len(checked) == 1
        assert len(adapter.seen_specs) == 1
        reopened(tmp_path / "semantic", runtime)
        save(f"t3-{fault}", {"checked": checked, "sandbox_calls": len(adapter.seen_specs)})
    finally:
        runtime.close()


async def test_r2_publication_fault_then_same_key_replay_and_new_key_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project_id, thread_id = await prepare_a04(tmp_path, allow_sandbox=True)
    try:
        before = domain_snapshot(runtime.ledger.engine)
        state = fault_once(monkeypatch, "_commit_outcome", RevisionCommitService, "commit")
        failed = await runtime.bus.dispatch(input_request("r2-retry", project_id, thread_id))
        assert failed.error is not None and state["hits"] == ["commit"]
        assert len(sandbox.seen_specs) == 1
        assert outcome_heads(runtime, project_id) == []
        faulted = domain_snapshot(runtime.ledger.engine)
        # The research phases before R2 are committed; the R2 publication is not: no Outcome
        # revision, no admission record, and the result artifact is still not eligible.
        assert all(
            row["entity_type"] != "OUTCOME" for row in added(before, faulted, "semantic_revisions")
        )
        assert control_kinds(before, faulted) == [
            ("BEHAVIOR_RUNTIME", "EXECUTION"),
            ("SANDBOX", "RECEIPT"),
            ("SANDBOX", "RUN"),
        ]
        staged = [
            row
            for row in added(before, faulted, "artifacts")
            if str(row["source_uri"]).startswith("sandbox://")
        ]
        assert len(staged) == 1 and staged[0]["cutoff_state"] == "PROHIBITED_CONTEXT"
        replay = await runtime.bus.dispatch(input_request("r2-retry", project_id, thread_id))
        assert replay.error == failed.error and len(sandbox.seen_specs) == 1
        replayed = domain_snapshot(runtime.ledger.engine)
        assert changed_tables(faulted, replayed) == []
        retried = value(
            await runtime.bus.dispatch(input_request("r2-retry-new", project_id, thread_id))
        )
        assert loop(retried)["terminal_state"] == "COMPLETED"
        assert len(sandbox.seen_specs) == 2 and len(outcome_heads(runtime, project_id)) == 1
        save(
            "t1-replay-retry",
            {
                "fault_request_control_kinds": control_kinds(before, faulted),
                "fault_request_changed_tables": changed_tables(before, faulted),
                "sandbox_calls": [1, 1, 2],
            },
        )
    finally:
        runtime.close()


async def test_r2_recovery_fault_then_same_key_replay_and_new_key_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = RecoverySandboxAdapter((SEMANTIC, SEMANTIC))
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path, "semantic", adapter, max_retries=2
    )
    try:
        assert isinstance(adapter.results, deque)
        state = fault_once(
            monkeypatch, "_failure_execution", SqliteLedgerTransaction, "insert_receipt"
        )
        failed = await runtime.bus.dispatch(input_request("r2-t3-retry", project_id, thread_id))
        assert failed.error is not None and state["hits"] == ["insert_receipt"]
        assert len(adapter.seen_specs) == 1
        faulted = domain_snapshot(runtime.ledger.engine)
        replay = await runtime.bus.dispatch(input_request("r2-t3-retry", project_id, thread_id))
        assert replay.error == failed.error and len(adapter.seen_specs) == 1
        assert changed_tables(faulted, domain_snapshot(runtime.ledger.engine)) == []
        retried = value(
            await runtime.bus.dispatch(input_request("r2-t3-retry-new", project_id, thread_id))
        )
        closed = loop(retried)
        assert closed["terminal_state"] == "CANDIDATE_REVISION_CREATED"
        assert closed["recovery"]["candidate_branch_revision_ids"]
        assert len(adapter.seen_specs) == 2
        save("t3-replay-retry", {"sandbox_calls": [1, 1, 2]})
    finally:
        runtime.close()
