from collections.abc import Callable
from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_restore_preview_contract import (
    prepared,
    record,
    restore_handler,
    revise,
    rpc_record,
)

from thoth.application.commands.restore import RestoreHandlers
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.operation import InternalFailureStateSnapshot, OperationRecord
from thoth.domain.restore import RestoreSelection
from thoth.domain.revision import SemanticRevision
from thoth.protocol.jsonrpc import JsonRpcRequest

CHANGES: dict[str, tuple[str, object]] = {
    "evidence-assessment.v1": ("decision_question", "Changed question"),
    "hypothesis.v1": ("statement", "Changed hypothesis"),
    "hypothesis-portfolio.v1": ("quality_gaps", ["Changed gap"]),
    "action.v1": ("specification", {"description": "Changed action"}),
    "action-portfolio.v1": ("decision_need", "Changed need"),
    "action-plan.v1": ("validation_state", "REVIEW_REQUIRED"),
}


def handler(runtime: AppRuntime) -> RestoreHandlers:
    result = restore_handler(runtime)
    # Controlled contract tests precede public readiness. This is not a runtime option.
    result.planner.apply_ready = True
    return result


async def input_for(
    runtime: AppRuntime, revision: SemanticRevision, changed: SemanticRevision, key: str
) -> dict[str, object]:
    selected = RestoreSelection(
        project_id="p",
        entity_type=revision.entity_type,
        entity_id=revision.entity_id,
        target_revision_digest=revision.revision_digest,
        expected_current_head=changed.revision_digest,
    )
    selection: dict[str, object] = {
        "project_id": "p",
        "entity_type": selected.entity_type.value,
        "entity_id": selected.entity_id,
        "target_revision_digest": selected.target_revision_digest,
        "expected_current_head": selected.expected_current_head,
    }
    preview = rpc_record(
        await runtime.bus.query(
            request(
                "revision/restore/preview",
                key,
                {
                    "project_id": "p",
                    "selection": selection,
                    "contract_version": 2,
                },
            )
        )
    )
    assert preview["availability"] == "AVAILABLE", preview
    basis_digest = preview["basis_digest"]
    assert isinstance(basis_digest, str)
    return {
        "project_id": "p",
        "selection": selection,
        "preview_basis_digest": basis_digest,
        "reason": "Restore checked contents",
    }


@pytest.mark.parametrize("profile", tuple(CHANGES))
async def test_apply_preserves_old_bytes_new_head_and_replays_once(
    tmp_path: Path, profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, model, accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates[profile]
        changed = revise(runtime, revision, snap, *CHANGES[profile])
        payload = await input_for(runtime, revision, changed, "preview")
        calls = len(model.calls)
        command = request("revision/restore/apply", "apply-once", payload)
        errors: list[Exception] = []

        def capture_internal_failure(
            *,
            operation: OperationRecord,
            request: JsonRpcRequest,
            exc: Exception,
            pre_state: InternalFailureStateSnapshot | None,
        ) -> None:
            errors.append(exc)

        monkeypatch.setattr(runtime.bus, "_persist_internal_failure", capture_internal_failure)
        response = await runtime.bus.dispatch(command)
        if errors:
            raise errors[0]
        applied = rpc_record(response)
        assert applied["status"] == "APPLIED"
        assert applied["selection"] == payload["selection"]
        assert applied["reanalysis"] == "NOT_REQUESTED"
        new_digest = applied["new_revision_digest"]
        assert isinstance(new_digest, str)
        new = runtime.ledger.read_revision_by_digest("p", new_digest)
        assert new is not None
        assert new.parent_revision_digests == (changed.revision_digest,)
        restored_snapshot = runtime.ledger.read_snapshot(new.snapshot_id)
        assert restored_snapshot is not None
        assert restored_snapshot.content == snap.content
        assert runtime.ledger.read_snapshot(snap.snapshot_id) == snap
        assert (
            runtime.ledger.read_dependency_states("p")[
                f"{new.entity_type.value}:{new.entity_id}"
            ].value
            == "STALE"
        )
        after = snapshot(runtime.ledger.engine)
        assert rpc_record(await runtime.bus.dispatch(command)) == applied
        assert_phase_delta(after, snapshot(runtime.ledger.engine))
        loser = request("revision/restore/apply", "second-writer", payload)
        denied = await runtime.bus.dispatch(loser)
        assert denied.error is not None
        assert denied.error.data["reason_code"] == "RESTORE_HEAD_CHANGED"
        assert_phase_delta(
            after,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, loser),
        )
        thread = rpc_record(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "currentness",
                    {"project_id": "p", "thread_id": accepted["thread_id"], "contract_version": 2},
                )
            )
        )
        assert thread["current_result"] is None
        assert record(thread["basis_currentness"])["state"] == "REVIEW_REQUIRED"
        assert len(model.calls) == calls
        if profile != "evidence-assessment.v1":
            method, identifier, root = {
                "hypothesis.v1": ("hypothesis/read", "hypothesis_id", "hypothesis"),
                "hypothesis-portfolio.v1": (
                    "hypothesis/portfolio/read",
                    "portfolio_id",
                    "portfolio",
                ),
                "action.v1": ("action/read", "action_id", "action"),
                "action-portfolio.v1": ("action/portfolio/read", "portfolio_id", "portfolio"),
                "action-plan.v1": ("action/plan/read", "plan_id", "plan"),
            }[profile]
            owner = record(rpc_record(
                await runtime.bus.dispatch(
                    request(
                        method,
                        "restored-owner",
                        {"project_id": "p", identifier: revision.entity_id},
                    )
                )
            )[root])
            assert owner["revision_digest"] == applied["new_revision_digest"]
        restore_audit_id = applied["restore_audit_id"]
        assert isinstance(restore_audit_id, str)
        audit = host.publication.controls.read("p", "REVISION", restore_audit_id)
        assert audit is not None and audit.record_type == "RESTORE_RECEIPT"
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "fail_at",
    (
        "snapshot",
        "revision",
        "head",
        "impact",
        "receipt",
        "baseline",
        "audit",
        "event",
        "checkpoint",
        "complete",
    ),
)
async def test_failure_after_publication_participant_rolls_back_entire_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    runtime, _model, _accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snap, *CHANGES["hypothesis.v1"])
        baseline_service = host.publication.baselines
        projection = baseline_service.refresh(
            project_id="p", thread_id=None, purpose="Fault fixture"
        )
        candidate = next(
            item
            for item in projection.baseline_candidates
            if item.head_map.get(f"HYPOTHESIS:{revision.entity_id}") == changed.revision_digest
        )
        _, baseline = baseline_service.decide(
            project_id="p",
            candidate_id=candidate.candidate_id,
            decision="APPROVE",
            actor_ref="human:fixture-owner",
            role_assignment_ref="role:fixture-owner",
            approved_digest=candidate.candidate_digest,
        )
        assert baseline is not None and baseline.lifecycle == "CURRENT"
        payload = await input_for(runtime, revision, changed, "preview")
        before = snapshot(runtime.ledger.engine)
        from thoth.adapters.storage.sqlite import SqliteLedgerTransaction

        participant, method = {
            "snapshot": (SqliteLedgerTransaction, "insert_snapshot"),
            "revision": (SqliteLedgerTransaction, "insert_revision"),
            "head": (SqliteLedgerTransaction, "set_head"),
            "impact": (SqliteLedgerTransaction, "apply_impact_plan"),
            "receipt": (SqliteLedgerTransaction, "insert_receipt"),
            "baseline": (host.publication.baselines, "refresh"),
            "audit": (host.publication.controls, "create"),
            "event": (host.publication.events, "append"),
            "checkpoint": (host.publication.events, "checkpoint"),
            "complete": (host.publication.operations, "complete"),
        }[fail_at]
        original: Callable[..., object] = getattr(participant, method)
        assert callable(original)
        reached: list[object] = []

        def fail_after(*args: object, **kwargs: object) -> None:
            result = original(*args, **kwargs)
            if fail_at in {"baseline", "audit", "event", "checkpoint", "complete"}:
                observed = baseline_service.read_set("p", baseline.baseline_set_digest)
                assert observed is not None
                assert observed.lifecycle == "STALE" and observed.recalculation_required
            reached.append(result)
            raise RuntimeError("INJECTED_AFTER_" + fail_at)

        monkeypatch.setattr(participant, method, fail_after)
        command = request("revision/restore/apply", "fault", payload)
        response = await runtime.bus.dispatch(command)
        assert response.error is not None and reached
        assert_phase_delta(
            before,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, command),
        )
        restored_baseline = baseline_service.read_set("p", baseline.baseline_set_digest)
        assert restored_baseline is not None and restored_baseline.lifecycle == "CURRENT"
        after_failure = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    monkeypatch.undo()
    from tests.integration.scoped_runtime import fixture_scope_policy
    from tests.integration.test_research_request_v2 import ControlledResearchModel

    from thoth.apps.runtime import create_runtime

    reopened = create_runtime(
        tmp_path,
        model_resolver=ControlledResearchModel(),
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        assert_phase_delta(after_failure, snapshot(reopened.ledger.engine))
    finally:
        reopened.close()
