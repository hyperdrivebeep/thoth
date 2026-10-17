"""99-V: public Improvement handler writes roll back together (test only).

Run original storage writes before faulting, including the final change-set append after
baseline promotion. Keep control_records in whole-domain snapshots; the existing snapshot
helper excludes only the five operation bookkeeping tables. Reopen with an independent engine
without runtime bootstrap. Models are scripted and pair comparison runs local bounded programs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine
from tests.integration.behavior_exposure_helpers import ExposureHarness, exposure_harness
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    prepare_project,
    request,
    value,
)

from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.apps.runtime import AppRuntime
from thoth.domain.behavior_execution import BehaviorBaselineRevision
from thoth.domain.control_record import ControlRecord

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step99-20261015"
Snapshot = dict[str, tuple[str, ...]]
FAULT = "99-V injected after targeted append"


def save(name: str, before: Snapshot, after: Snapshot, writes: Writes) -> None:
    def digest(snapshot: Snapshot) -> str:
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v99-{name}.json").write_text(
        json.dumps(
            {
                "before_sha256": digest(before),
                "after_sha256": digest(after),
                "changed_tables": [table for table in before if before[table] != after[table]],
                "fault_hits": writes.hits,
                "write_order": writes.order,
                "appended": [
                    {
                        "namespace": r.namespace,
                        "record_type": r.record_type,
                        "record_id": r.record_id,
                        "state": r.state,
                        "version": r.version,
                    }
                    for r in writes.appended
                ],
                "baseline_promote_count": len(writes.baselines),
                "reopened_snapshot_equal": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def check_reopened(engine: Engine, expected: Snapshot) -> None:
    reopened = create_engine(engine.url)
    try:
        assert domain_snapshot(reopened) == expected
    finally:
        reopened.dispose()


@dataclass
class Writes:
    staged: list[ControlRecord] = field(default_factory=list)
    appended: list[ControlRecord] = field(default_factory=list)
    baselines: list[BehaviorBaselineRevision] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    hits: int = 0


def observe_writes(
    patch: pytest.MonkeyPatch,
    target: Callable[[ControlRecord], bool],
    *,
    fail: bool,
) -> Writes:
    """Capture the generated target id at stage, then filter that exact id on append."""
    seen = Writes()
    original_stage = ControlRecordService.stage
    original_append = SqliteControlRecordStore.append
    original_promote = SqliteBehaviorExecutionStore.promote

    def stage(service: ControlRecordService, **kwargs: Any) -> ControlRecord:
        record = original_stage(service, **kwargs)
        if target(record):
            seen.staged.append(record)
        return record

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        original_append(store, record)
        seen.appended.append(record)
        seen.order.append(f"{record.namespace}/{record.record_type}/{record.state}")
        identities = {
            (r.namespace, r.record_type, r.record_id, r.control_revision_id) for r in seen.staged
        }
        identity = (
            record.namespace,
            record.record_type,
            record.record_id,
            record.control_revision_id,
        )
        if identity in identities and fail:
            seen.hits += 1
            raise RuntimeError(FAULT)

    def promote(
        store: SqliteBehaviorExecutionStore,
        record: BehaviorBaselineRevision,
        *,
        expected_revision: int,
    ) -> None:
        original_promote(store, record, expected_revision=expected_revision)
        seen.baselines.append(record)
        seen.order.append("BASELINE/PROMOTED")

    patch.setattr(ControlRecordService, "stage", stage)
    patch.setattr(SqliteControlRecordStore, "append", append)
    patch.setattr(SqliteBehaviorExecutionStore, "promote", promote)
    return seen


def proposal_body(project: str) -> dict[str, object]:
    return {
        "project_id": project,
        "target_component": "PROMPT",
        "scope_key": "99-v-draft",
        "trigger_refs": [],
        "baseline_digest": "a" * 64,
        "candidate_content": {"prompt_patch": "test-only"},
        "improvement_hypothesis": "Check rollback of the public draft",
        "evaluation_contract_ref": "99-v-contract",
    }


async def fault_single_write(
    runtime: AppRuntime,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    body: dict[str, object],
    record_type: str,
    record_id: str | None,
) -> None:
    before = domain_snapshot(runtime.ledger.engine)

    def target(record: ControlRecord) -> bool:
        return (
            record.namespace == "IMPROVEMENT"
            and record.record_type == record_type
            and record.project_id == body["project_id"]
            and (
                record.record_id == record_id
                if record_id is not None
                else record.payload.get("scope_key") == body["scope_key"]
            )
        )

    with monkeypatch.context() as patch:
        writes = observe_writes(patch, target, fail=True)
        failed = await runtime.bus.dispatch(request(method, "99-v-fault", body))
    assert failed.error is not None
    assert writes.hits == 1 and len(writes.staged) == 1
    assert writes.appended == writes.staged
    assert writes.baselines == []
    after = domain_snapshot(runtime.ledger.engine)
    assert after == before, "HOLD_DEFECT: public handler left partial domain writes"
    check_reopened(runtime.ledger.engine, before)
    save(method.replace("/", "-"), before, after, writes)


@pytest.mark.parametrize("mode", ["propose", "revise"])
async def test_draft_append_fault_preserves_every_domain_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        body: dict[str, object] = proposal_body(project)
        identifier = None
        if mode == "revise":
            proposal = value(
                await runtime.bus.dispatch(request("improvement/propose", "setup-proposal", body))
            )["improvement"]
            identifier = str(proposal["record_id"])
            body = {
                "project_id": project,
                "improvement_revision_id": identifier,
                "expected_revision_digest": proposal["record_digest"],
                "patch": {"candidate_content": {"prompt_patch": "revised-test-only"}},
                "evidence_refs": [],
                "reason": "99-V",
            }
        await fault_single_write(
            runtime, monkeypatch, f"improvement/{mode}", body, "REVISION", identifier
        )
    finally:
        runtime.close()


async def test_assessment_append_fault_preserves_pair_and_previous_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": 1},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path,
        {"op": "literal", "value": 0},
        {"op": "literal", "value": 1},
        cases,
    ) as h:
        result = (await h.run())["pair"]["result"]
        assert result is not None
        await fault_single_write(
            h.runtime,
            monkeypatch,
            "improvement/evaluation/assess",
            {
                "project_id": h.project,
                "evaluation_plan_id": h.plan["record_id"],
                "expected_evaluation_revision": h.plan["version"],
                "result_refs": [result["pair_id"]],
                "evaluator_result_refs": [result["result_digest"]],
                "exposure_ledger_ref": result["exposure_id"],
            },
            "EVALUATION_PLAN",
            str(h.plan["record_id"]),
        )


async def ready_promotion(h: ExposureHarness) -> dict[str, Any]:
    await h.arm()
    value(await h.call("thread/input", "99-v-trial", {"thread_id": h.thread}))
    completed = await h.finish_trial()
    assert completed["state"] == "COMPLETED"
    promotion = await h.prepare_baseline()
    assert promotion["state"] == "PENDING"
    return promotion


def decision_body(
    h: ExposureHarness, promotion: dict[str, Any], decision: str
) -> dict[str, object]:
    return {
        "promotion_candidate_id": promotion["record_id"],
        "decision": decision,
        "approved_digest": promotion["record_digest"],
        "actor_ref": "human:policy-owner",
        "role_assignment_ref": h.role,
    }


def final_append_target(
    h: ExposureHarness, promotion: dict[str, Any]
) -> Callable[[ControlRecord], bool]:
    return lambda record: (
        record.project_id == h.pair.project
        and record.namespace == "IMPROVEMENT"
        and record.record_type == "REVISION_CHANGE_SET_PROPOSAL"
        and record.payload.get("promotion_id") == promotion["record_id"]
    )


def check_decision_order(
    writes: Writes, promotion: dict[str, Any], decision: str, *, fault: bool
) -> None:
    approved = decision == "APPROVE"
    state = "APPROVED" if approved else "REJECTED"
    change_state = "APPLIED_BEHAVIOR_BASELINE" if approved else "PROPOSED_NOT_APPLIED"
    assert writes.hits == int(fault)
    assert len(writes.staged) == 1
    assert writes.order == [
        f"IMPROVEMENT/PROMOTION/{state}",
        *(["BASELINE/PROMOTED"] if approved else []),
        f"IMPROVEMENT/REVISION_CHANGE_SET_PROPOSAL/{change_state}",
    ]
    assert len(writes.baselines) == int(approved)
    first, last = writes.appended
    assert first.record_id == promotion["record_id"]
    assert first.version == promotion["version"] + 1
    assert last == writes.staged[0]
    assert last.payload["promotion_id"] == first.record_id
    assert last.payload["current_state_mutated"] is approved


def check_pending(h: ExposureHarness, promotion: dict[str, Any]) -> None:
    stored = SqliteControlRecordStore(h.pair.runtime.ledger.engine).read(
        h.pair.project, "IMPROVEMENT", promotion["record_id"]
    )
    assert stored is not None and stored.model_dump(mode="json") == promotion


def check_success_delta(
    h: ExposureHarness,
    before: Snapshot,
    after: Snapshot,
    writes: Writes,
    result: dict[str, Any],
    decision: str,
) -> None:
    approved = decision == "APPROVE"
    for table in before:
        if table not in {"control_records", "behavior_baseline_history"}:
            assert before[table] == after[table], table
    assert set(before["control_records"]).issubset(after["control_records"])
    added = set(after["control_records"]) - set(before["control_records"])
    actual = [ControlRecord.model_validate_json(json.loads(row)["content_json"]) for row in added]
    assert {r.control_revision_id: r for r in actual} == {
        r.control_revision_id: r for r in writes.appended
    }
    assert all(r.namespace == "IMPROVEMENT" for r in actual)
    assert {r.record_type for r in actual} == {"PROMOTION", "REVISION_CHANGE_SET_PROPOSAL"}
    before_baselines, after_baselines = (
        before["behavior_baseline_history"],
        after["behavior_baseline_history"],
    )
    assert set(before_baselines).issubset(after_baselines)
    assert len(after_baselines) == len(before_baselines) + int(approved)
    assert result["promotion"] == writes.appended[0].model_dump(mode="json")
    assert result["revision_baseline_change_set_proposal"] == (
        writes.appended[1].model_dump(mode="json")
    )
    assert result["current_state_mutated"] is approved
    if approved:
        baseline = result["behavior_baseline_revision"]
        assert baseline == writes.baselines[0].model_dump(mode="json")
        assert baseline["content_digest"] == h.exposure["spec"]["candidate_digest"]
        assert writes.appended[1].payload["behavior_baseline_receipt"] == baseline["receipt_digest"]
        stored = SqliteBehaviorExecutionStore(h.pair.runtime.ledger.engine).baselines(
            h.pair.project, h.pair.binding.component, "LOCAL"
        )
        assert len(stored) == 1 and stored[0] == writes.baselines[0]
    else:
        assert after_baselines == before_baselines
        assert result["behavior_baseline_revision"] is None


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
async def test_final_change_set_fault_rolls_back_decision_and_baseline_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    async with exposure_harness(tmp_path) as h:
        promotion = await ready_promotion(h)
        before = domain_snapshot(h.pair.runtime.ledger.engine)
        model_calls = len(h.model.guidance)
        with monkeypatch.context() as patch:
            writes = observe_writes(patch, final_append_target(h, promotion), fail=True)
            failed = await h.call(
                "improvement/promotion/decide",
                "99-v-final-fault",
                decision_body(h, promotion, decision),
            )
        assert failed.error is not None
        check_decision_order(writes, promotion, decision, fault=True)
        after = domain_snapshot(h.pair.runtime.ledger.engine)
        assert after == before, "HOLD_DEFECT: decision and baseline did not roll back together"
        assert len(h.model.guidance) == model_calls
        check_pending(h, promotion)
        check_reopened(h.pair.runtime.ledger.engine, before)
        save(f"final-fault-{decision}", before, after, writes)


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
async def test_decision_success_publishes_only_the_expected_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    async with exposure_harness(tmp_path) as h:
        promotion = await ready_promotion(h)
        before = domain_snapshot(h.pair.runtime.ledger.engine)
        with monkeypatch.context() as patch:
            writes = observe_writes(patch, final_append_target(h, promotion), fail=False)
            result = value(
                await h.call(
                    "improvement/promotion/decide",
                    "99-v-success",
                    decision_body(h, promotion, decision),
                )
            )
        check_decision_order(writes, promotion, decision, fault=False)
        after = domain_snapshot(h.pair.runtime.ledger.engine)
        check_success_delta(h, before, after, writes, result, decision)
        check_reopened(h.pair.runtime.ledger.engine, after)
        save(f"success-{decision}", before, after, writes)


async def test_failed_approval_replays_without_writes_and_new_key_promotes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        promotion = await ready_promotion(h)
        body = decision_body(h, promotion, "APPROVE")
        before = domain_snapshot(h.pair.runtime.ledger.engine)
        calls: list[str] = []
        original = ImprovementExposureService.prepare_promotion

        def prepare(
            service: ImprovementExposureService, project: str, identifier: str, digest: str
        ) -> Any:
            calls.append(identifier)
            return original(service, project, identifier, digest)

        monkeypatch.setattr(ImprovementExposureService, "prepare_promotion", prepare)
        model_calls = len(h.model.guidance)
        with monkeypatch.context() as patch:
            writes = observe_writes(patch, final_append_target(h, promotion), fail=True)
            failed = await h.call("improvement/promotion/decide", "99-v-replay", body)
        assert failed.error is not None
        check_decision_order(writes, promotion, "APPROVE", fault=True)
        assert domain_snapshot(h.pair.runtime.ledger.engine) == before
        with monkeypatch.context() as patch:
            replay_writes = observe_writes(patch, final_append_target(h, promotion), fail=False)
            replay = await h.call("improvement/promotion/decide", "99-v-replay", body)
        assert replay.error == failed.error
        assert calls == [promotion["record_id"]]
        assert replay_writes.order == [] and replay_writes.staged == []
        assert domain_snapshot(h.pair.runtime.ledger.engine) == before
        check_pending(h, promotion)
        check_reopened(h.pair.runtime.ledger.engine, before)
        save("same-key-replay", before, before, replay_writes)
        with monkeypatch.context() as patch:
            retried_writes = observe_writes(patch, final_append_target(h, promotion), fail=False)
            retried = value(await h.call("improvement/promotion/decide", "99-v-new-key", body))
        assert calls == [promotion["record_id"], promotion["record_id"]]
        assert len(h.model.guidance) == model_calls
        check_decision_order(retried_writes, promotion, "APPROVE", fault=False)
        after = domain_snapshot(h.pair.runtime.ledger.engine)
        check_success_delta(h, before, after, retried_writes, retried, "APPROVE")
        check_reopened(h.pair.runtime.ledger.engine, after)
        save("new-key-retry", before, after, retried_writes)
