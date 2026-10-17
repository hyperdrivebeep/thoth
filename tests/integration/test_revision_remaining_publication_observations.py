"""D publication atomicity: complete rollback, error replay, and one successful fresh-key write."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_m5_improvement_handlers_atomicity import Snapshot, check_reopened
from tests.integration.test_m5_revision_control_atomicity import (
    Harness,
    assert_added_controls,
    assert_new_control_scopes,
    commit_body,
    control_rows,
    harness,
)
from tests.integration.test_revision_merge_late_write_observation import (
    assert_failed_operation,
    assert_merge_retry,
    assert_reached_merge_tail,
    forbid_independent_baseline_writes,
    prepare_siblings,
)
from tests.integration.test_revision_merge_late_write_observation import install as install_o1

from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.domain.control_record import ControlRecord
from thoth.protocol.registry import MethodRegistry

OUT = Path(__file__).resolve().parents[2] / "outputs/d-revision-publication-20261015"


@dataclass
class Scenario:
    kind: str
    method: str
    body: dict[str, object]
    target_id: str


def latest(snapshot: Snapshot, record_type: str) -> list[ControlRecord]:
    records: dict[str, ControlRecord] = {}
    for record in control_rows(snapshot).values():
        if record.namespace == "REVISION" and record.record_type == record_type:
            previous = records.get(record.record_id)
            if previous is None or previous.version < record.version:
                records[record.record_id] = record
    return sorted(records.values(), key=lambda r: r.record_id)


async def prepare_resolve(h: Harness) -> Scenario:
    proposals = [await h.propose("o2-left"), await h.propose("o2-right")]
    results: list[dict[str, Any]] = []
    for label, proposal in zip(("left", "right"), proposals, strict=True):
        change = await h.change(proposal["record_digest"], f"o2-{label}-change")
        checked = await h.validate(change, f"o2-{label}-validate")
        results.append(
            value(
                await h.call(
                    "revision/changeSet/commit",
                    f"o2-{label}-commit",
                    commit_body(h, change, checked),
                )
            )
        )
    assert results[0]["change_set"]["state"] == "COMMITTED"
    assert results[1]["change_set"]["state"] == "BRANCHED"
    merged = value(
        await h.call(
            "revision/merge/propose",
            "o2-conflict",
            {
                "aggregate_id": h.object_id,
                "parent_revision_digests": [
                    results[0]["new_project_head_set"][h.key],
                    results[1]["branch_revision_digests"][0],
                ],
                "merge_policy_ref": "merge:three-way-v1",
                "reason": "O2 conflicting purpose edits",
                "evidence_refs": [],
            },
        )
    )
    assert merged["semantic_merge"]["state"] == "OPEN_CONFLICT"
    assert merged["semantic_merge"]["conflict_paths"] == ["/purpose_statement"]
    proposal = merged["merge_proposal"]
    return Scenario(
        "o2",
        "revision/merge/resolve",
        {
            "merge_proposal_id": proposal["record_id"],
            "resolved_content": {**h.content, "purpose_statement": "O2 chosen resolution"},
            "resolution_map": {"purpose_statement": "O2 chosen resolution"},
            "evidence_refs": [],
            "actor_or_agent_ref": "agent:102-v",
            "expected_conflict_revision": merged["conflict"]["version"],
        },
        proposal["record_id"],
    )


async def prepare_baseline(h: Harness) -> Scenario:
    project = value(await h.call("project/read", "o3-project", {}))
    role = value(
        await h.call(
            "project/role/assign",
            "o3-owner-role",
            {
                "expected_revision": project["revision"],
                "actor_id": "human:o3-owner",
                "role": "baseline-owner",
                "scope": "PROJECT",
                "authority_tags": ["BASELINE_OWNER"],
            },
        )
    )["role"]
    authority = {
        "decision": "APPROVE",
        "actor_ref": "human:o3-owner",
        "role_assignment_ref": role["role_assignment_id"],
    }
    # This minimal no-model harness starts no analysis, so seed a nonempty typed baseline
    # through the runtime's real service before observing the separate legacy public route.
    registry = vars(h.runtime.bus)["_registry"]
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("revision/baseline/decide")
    assert isinstance(bound, MethodType) and isinstance(bound.__self__, RevisionFullHandlers)
    baselines = vars(bound.__self__)["_baselines"]
    assert isinstance(baselines, BaselineService)
    baselines.refresh(project_id=h.project, thread_id=None, purpose="O3 typed control fixture")
    listed = value(await h.call("revision/baseline/list", "o3-typed-candidates", {}))
    assert listed["typed_baseline_candidates"]
    candidate = listed["typed_baseline_candidates"][0]
    typed = value(
        await h.call(
            "revision/baseline/decide",
            "o3-typed-seed",
            {
                **authority,
                "baseline_candidate_id": candidate["candidate_id"],
                "approved_digest": candidate["candidate_digest"],
            },
        )
    )
    assert typed["typed_baseline_set"]["lifecycle"] == "CURRENT"
    bodies: list[dict[str, object]] = []
    for label in ("old", "new"):
        candidate = value(
            await h.call(
                "revision/baseline/prepare",
                f"o3-{label}-prepare",
                {
                    "purpose": f"O3 legacy {label}",
                    "scoped_head_map": h.heads,
                    "policy_version": "baseline:o3",
                    "evidence_refs": [],
                },
            )
        )["baseline_candidate"]
        bodies.append(
            {
                **authority,
                "baseline_candidate_id": candidate["record_id"],
                "approved_digest": candidate["record_digest"],
            }
        )
    old = value(await h.call("revision/baseline/decide", "o3-old-approve", bodies[0]))
    assert old["baseline"]["state"] == "CURRENT"
    assert len(h.snapshot()["baseline_sets"]) == 1
    return Scenario("o3", "revision/baseline/decide", bodies[1], old["baseline"]["record_id"])


async def prepare(h: Harness, kind: str) -> Scenario:
    scenario = await (prepare_resolve(h) if kind == "o2" else prepare_baseline(h))
    registry = vars(h.runtime.bus)["_registry"]
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve(scenario.method)
    assert isinstance(bound, MethodType) and isinstance(bound.__self__, RevisionFullHandlers)
    expected = (
        RevisionFullHandlers.merge_resolve if kind == "o2" else RevisionFullHandlers.baseline_decide
    )
    assert bound.__func__ is expected
    return scenario


@dataclass
class Probe:
    controls: list[ControlRecord] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    hits: int = 0


def install(patch: pytest.MonkeyPatch, h: Harness, scenario: Scenario, *, fail: bool) -> Probe:
    forbid_independent_baseline_writes(patch)
    probe = Probe()
    original = SqliteControlRecordStore.append

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        original(store, record)
        if record.project_id != h.project or record.namespace != "REVISION":
            return
        probe.controls.append(record)
        probe.order.append(f"{record.record_type}:{record.state}")
        target = (
            record.record_type == "MERGE_PROPOSAL"
            and record.record_id == scenario.target_id
            and record.state == "RESOLVED"
            if scenario.kind == "o2"
            else record.record_type == "BASELINE"
            and record.state == "CURRENT"
            and record.payload.get("candidate_digest") == scenario.body["approved_digest"]
        )
        if fail and target:
            stored = store.read_digest(h.project, record.record_digest)
            assert stored is not None
            assert stored.model_dump(mode="json") == record.model_dump(mode="json")
            probe.hits += 1
            raise RuntimeError(f"102 {scenario.kind} after original final append")

    patch.setattr(SqliteControlRecordStore, "append", append)
    return probe


def delta(before: Snapshot, after: Snapshot) -> dict[str, object]:
    return {
        table: {
            "removed": [json.loads(r) for r in sorted(set(before[table]) - set(after[table]))],
            "added": [json.loads(r) for r in sorted(set(after[table]) - set(before[table]))],
        }
        for table in before
        if before[table] != after[table]
    }


def save(name: str, **report: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"o3-remaining-{name}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def snapshot_digest(snapshot: Snapshot) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("kind", ["o2", "o3"])
async def test_remaining_success_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async with harness(tmp_path) as h:
        scenario = await prepare(h, kind)
        before = h.snapshot()
        with monkeypatch.context() as patch:
            probe = install(patch, h, scenario, fail=False)
            result = value(await h.call(scenario.method, "control", scenario.body))
        after = h.snapshot()
        if kind == "o2":
            assert result["merge_proposal"]["state"] == "RESOLVED"
            assert probe.order == ["PROPOSAL:DRAFT", "CHANGE_SET:STAGED", "MERGE_PROPOSAL:RESOLVED"]
        else:
            assert probe.order == ["BASELINE:SUPERSEDED", "BASELINE:CURRENT"]
            assert result["baseline"]["state"] == "CURRENT"
            assert before["baseline_sets"] == after["baseline_sets"]
        assert before["working_heads"] == after["working_heads"]
        check_reopened(h.runtime.ledger.engine, after)
        save(
            kind + "-control",
            verdict="CONTROL_PASS",
            order=probe.order,
            result=result,
            delta=delta(before, after),
            model_calls=h.model.calls,
        )


@pytest.mark.parametrize("kind", ["o2", "o3"])
async def test_remaining_fault_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async with harness(tmp_path) as h:
        scenario = await prepare(h, kind)
        before = h.snapshot()
        with monkeypatch.context() as patch:
            probe = install(patch, h, scenario, fail=True)
            failed = await h.call(scenario.method, "fault", scenario.body)
            assert failed.error is not None and probe.hits == 1
            expected = (
                ["PROPOSAL:DRAFT", "CHANGE_SET:STAGED", "MERGE_PROPOSAL:RESOLVED"]
                if kind == "o2"
                else ["BASELINE:SUPERSEDED", "BASELINE:CURRENT"]
            )
            assert probe.order == expected
            after = h.snapshot()
            check_reopened(h.runtime.ledger.engine, after)
            order = list(probe.order)
            replay = await h.call(scenario.method, "fault", scenario.body)
            assert replay.error == failed.error and probe.order == order and probe.hits == 1
            assert h.snapshot() == after
        save(
            kind + "-rollback",
            fault_hits=probe.hits,
            delta=delta(before, after),
            before_sha256=snapshot_digest(before),
            after_sha256=snapshot_digest(after),
        )
        assert after == before, f"D_RED_PARTIAL_PUBLICATION: {kind}"
        assert_failed_operation(
            h,
            failed,
            scenario.method,
            "fault",
            ["control:" + r.record_digest for r in probe.controls],
        )
        with monkeypatch.context() as patch:
            retry_probe = install(patch, h, scenario, fail=False)
            retried = await h.call(scenario.method, "new-key", scenario.body)
            result = value(retried)
            successful = h.snapshot()
            retry_order = list(retry_probe.order)
            again = await h.call(scenario.method, "new-key", scenario.body)
            assert value(again) == result and retry_probe.order == retry_order
            assert h.snapshot() == successful
        retried_snapshot = h.snapshot()
        assert retry_probe.order == expected and retry_probe.hits == 0
        assert_added_controls(before, retried_snapshot, retry_probe.controls)
        assert_new_control_scopes(h, before, retried_snapshot, retry_probe.controls)
        allowed = {
            "control_records",
            "resource_scope_heads",
            "resource_scope_history",
            "resource_scope_receipts",
        }
        for table in before:
            if table not in allowed:
                assert before[table] == retried_snapshot[table], table
        if kind == "o2":
            assert result["merge_proposal"]["state"] == "RESOLVED"
            assert len(retry_probe.controls) == 3
            assert result["candidate_revision"]["record_id"] == retry_probe.controls[0].record_id
            assert result["change_set"]["record_id"] == retry_probe.controls[1].record_id
            assert retry_probe.controls[2].record_id == scenario.target_id
        else:
            assert len(retry_probe.controls) == 2
            assert retry_probe.controls[0].record_id == scenario.target_id
            assert result["baseline"]["record_id"] == retry_probe.controls[1].record_id
            assert [r.state for r in latest(after, "BASELINE")] == ["CURRENT"]
            assert sum(r.state == "CURRENT" for r in latest(retried_snapshot, "BASELINE")) == 1
        assert not (
            {r.control_revision_id for r in probe.controls} & control_rows(retried_snapshot).keys()
        )
        check_reopened(h.runtime.ledger.engine, retried_snapshot)
        assert (
            before["working_heads"] == after["working_heads"] == retried_snapshot["working_heads"]
        )
        assert (
            before["baseline_sets"] == after["baseline_sets"] == retried_snapshot["baseline_sets"]
        )
        save(
            kind + "-fault-retries",
            verdict="PASS",
            fault_hits=probe.hits,
            fault_order=probe.order,
            fault_response=failed.model_dump(mode="json"),
            fault_delta=delta(before, after),
            before_sha256=snapshot_digest(before),
            after_fault_sha256=snapshot_digest(after),
            same_key_error_equal=True,
            same_key_additional_writes=0,
            fault_reopened_snapshot_equal=True,
            new_key_response=retried.model_dump(mode="json"),
            new_key_order=retry_probe.order,
            new_key_delta=delta(after, retried_snapshot),
            new_key_reopened_snapshot_equal=True,
            latest_after_fault={
                t: [r.model_dump(mode="json") for r in latest(after, t)]
                for t in ["MERGE_PROPOSAL", "CONFLICT", "BASELINE"]
            },
            latest_after_retry={
                t: [r.model_dump(mode="json") for r in latest(retried_snapshot, t)]
                for t in ["MERGE_PROPOSAL", "CONFLICT", "BASELINE"]
            },
            typed_baseline_rows_before=len(before["baseline_sets"]),
            typed_baselines_unchanged=True,
            canonical_heads_unchanged=True,
            model_calls=h.model.calls,
            new_key_success_replay_no_additional_writes=True,
        )
        assert h.model.calls == 0
        assert after == before


async def test_o1_fresh_key_after_failed_auto_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path) as h:
        body = await prepare_siblings(h)
        before = h.snapshot()
        with monkeypatch.context() as patch:
            probe = install_o1(patch, h, fail=True)
            failed = await h.call("revision/merge/propose", "fault", body)
            assert failed.error is not None and probe.hits == 1
            assert_reached_merge_tail(probe)
            after = h.snapshot()
            check_reopened(h.runtime.ledger.engine, after)
            order = list(probe.order)
            replay = await h.call("revision/merge/propose", "fault", body)
            assert replay.error == failed.error and probe.order == order and probe.hits == 1
            assert h.snapshot() == after
        save("o1-rollback", fault_hits=probe.hits, delta=delta(before, after))
        assert after == before, "D_RED_PARTIAL_PUBLICATION: O1"
        assessment = probe.assessments[0]
        assert assessment.merged_revision_digest is not None
        forbidden = ["control:" + r.record_digest for r in probe.controls]
        forbidden.extend(
            [
                "revision:" + assessment.merged_revision_digest,
                "receipt:" + probe.commits[0].receipt.receipt_digest,
            ]
        )
        assert_failed_operation(h, failed, "revision/merge/propose", "fault", forbidden)
        with monkeypatch.context() as patch:
            retry_probe = install_o1(patch, h, fail=False)
            retried = await h.call("revision/merge/propose", "new-key", body)
            result = value(retried)
            assert_merge_retry(h, before, retry_probe, result)
            successful = h.snapshot()
            order = list(retry_probe.order)
            again = await h.call("revision/merge/propose", "new-key", body)
            assert value(again) == result and retry_probe.order == order
            assert h.snapshot() == successful
        retried_snapshot = h.snapshot()
        check_reopened(h.runtime.ledger.engine, retried_snapshot)
        save(
            "o1-fault-retries",
            verdict="PASS",
            fault_hits=probe.hits,
            fault_order=probe.order,
            fault_response=failed.model_dump(mode="json"),
            fault_delta=delta(before, after),
            same_key_error_equal=True,
            same_key_additional_writes=0,
            fault_reopened_snapshot_equal=True,
            new_key_response=retried.model_dump(mode="json"),
            new_key_order=retry_probe.order,
            new_key_canonical_commits=len(retry_probe.commits),
            new_key_delta=delta(after, retried_snapshot),
            new_key_reopened_snapshot_equal=True,
            model_calls=h.model.calls,
            new_key_success_replay_no_additional_writes=True,
        )
        assert h.model.calls == 0
        assert after == before
