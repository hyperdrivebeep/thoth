"""D-REVISION-PUBLICATION: late merge failure rolls back; a new key publishes once."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from sqlalchemy import select
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_m5_improvement_handlers_atomicity import Snapshot, check_reopened
from tests.integration.test_m5_revision_control_atomicity import (
    Harness,
    assert_added_controls,
    commit_body,
    control_rows,
    harness,
)

from thoth.adapters.storage.baseline import SqliteBaselineStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.resource_scope_schema import operation_resource_bindings
from thoth.adapters.storage.schema import operations
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.application.services.semantic_merge import SemanticThreeWayMergeService
from thoth.domain.control_record import ControlRecord
from thoth.domain.revision import RevisionChangeSet, SemanticMergeResult
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry

OUT = Path(__file__).resolve().parents[2] / "outputs/d-revision-publication-20261015"
FAULT = "102 O3/O1 after original MERGE_RECEIPT append"


async def prepare_siblings(h: Harness) -> dict[str, object]:
    proposals: list[dict[str, Any]] = []
    for label, field_name in (("left", "purpose_statement"), ("right", "problem_frame")):
        proposals.append(
            value(
                await h.call(
                    "revision/propose",
                    f"o1-{label}-proposal",
                    {
                        "aggregate_id": h.object_id,
                        "aggregate_type": "DECISION_OBJECT",
                        "parent_revision_digests": [h.heads[h.key]],
                        "candidate_content": {**h.content, field_name: f"O1 {label} edit"},
                        "reason": f"O1 {label}",
                        "evidence_refs": [],
                        "actor_or_agent_ref": "agent:102-v",
                        "expected_head_digest": h.heads[h.key],
                        "candidate_schema_version": h.schema,
                    },
                )
            )["proposal"]
        )
    committed: list[dict[str, Any]] = []
    for label, proposal in zip(("left", "right"), proposals, strict=True):
        change = await h.change(proposal["record_digest"], f"o1-{label}-change")
        checked = await h.validate(change, f"o1-{label}-validate")
        assert checked["state"] == "READY_TO_COMMIT"
        committed.append(
            value(
                await h.call(
                    "revision/changeSet/commit",
                    f"o1-{label}-commit",
                    commit_body(h, change, checked),
                )
            )
        )
    assert committed[0]["change_set"]["state"] == "COMMITTED"
    assert committed[1]["change_set"]["state"] == "BRANCHED"
    left = committed[0]["new_project_head_set"][h.key]
    right = committed[1]["branch_revision_digests"]
    assert len(right) == 1 and h.runtime.ledger.read_heads(h.project)[h.key] == left
    registry = vars(h.runtime.bus)["_registry"]
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("revision/merge/propose")
    assert isinstance(bound, MethodType) and isinstance(bound.__self__, RevisionFullHandlers)
    assert bound.__func__ is RevisionFullHandlers.merge_propose
    return {
        "aggregate_id": h.object_id,
        "parent_revision_digests": [left, right[0]],
        "merge_policy_ref": "merge:three-way-v1",
        "reason": "O1 independently changed fields",
        "evidence_refs": [],
    }


@dataclass
class MergeProbe:
    order: list[str] = field(default_factory=list)
    controls: list[ControlRecord] = field(default_factory=list)
    commits: list[CommitResult] = field(default_factory=list)
    assessments: list[SemanticMergeResult] = field(default_factory=list)
    hits: int = 0
    raw_receipt_visible_before_fault: bool = False


def install(patch: pytest.MonkeyPatch, h: Harness, *, fail: bool) -> MergeProbe:
    forbid_independent_baseline_writes(patch)
    probe = MergeProbe()
    original_commit = RevisionCommitService.commit
    original_merge = SemanticThreeWayMergeService.merge
    original_refresh = BaselineService.refresh
    original_append = SqliteControlRecordStore.append

    def commit(service: RevisionCommitService, change: RevisionChangeSet) -> CommitResult:
        result = original_commit(service, change)
        assert change.project_id == h.project
        probe.commits.append(result)
        probe.order.append("canonical.commit:" + result.disposition.value)
        return result

    def merge(service: SemanticThreeWayMergeService, **kwargs: Any) -> SemanticMergeResult:
        result = original_merge(service, **kwargs)
        probe.assessments.append(result)
        probe.order.append("semantic.merge:" + result.state.value)
        return result

    def refresh(service: BaselineService, **kwargs: Any) -> Any:
        result = original_refresh(service, **kwargs)
        assert kwargs["project_id"] == h.project and kwargs["propose_candidates"] is False
        probe.order.append("baseline.refresh")
        return result

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        original_append(store, record)
        if record.project_id != h.project or record.namespace != "REVISION":
            return
        probe.controls.append(record)
        probe.order.append(f"control:{record.record_type}:{record.state}")
        if fail and record.record_type == "MERGE_RECEIPT" and record.state == "AUTO_MERGED":
            assert len(probe.assessments) == len(probe.commits) == 1
            assert record.payload["merge_id"] == probe.assessments[0].merge_id
            assert record.record_id == record.payload["receipt_id"]
            stored = store.read_digest(h.project, record.record_digest)
            assert stored is not None
            assert stored.model_dump(mode="json") == record.model_dump(mode="json")
            assert stored.record_digest == record.record_digest
            probe.raw_receipt_visible_before_fault = True
            probe.hits += 1
            raise RuntimeError(FAULT)

    patch.setattr(RevisionCommitService, "commit", commit)
    patch.setattr(SemanticThreeWayMergeService, "merge", merge)
    patch.setattr(BaselineService, "refresh", refresh)
    patch.setattr(SqliteControlRecordStore, "append", append)
    return probe


def forbid_independent_baseline_writes(patch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("independent baseline writer entered the D publication scope")

    patch.setattr(SqliteBaselineStore, "add_candidate", forbidden)
    patch.setattr(SqliteBaselineStore, "commit_decision", forbidden)


def assert_failed_operation(
    h: Harness, response: JsonRpcResponse, method: str, key: str, forbidden_refs: list[str]
) -> None:
    assert response.error is not None and response.error.data is not None
    operation_id = response.error.data["operation_id"]
    with h.runtime.ledger.engine.connect() as connection:
        row = (
            connection.execute(select(operations).where(operations.c.operation_id == operation_id))
            .mappings()
            .one()
        )
        assert row["project_id"] == h.project and row["method"] == method
        assert row["idempotency_key"] == key and row["state"] == "FAILED"
        assert row["result_json"] is None
        assert json.loads(row["error_json"]) == response.error.model_dump(mode="json")
        bindings = (
            connection.execute(
                select(operation_resource_bindings.c.content_json).where(
                    operation_resource_bindings.c.operation_id == operation_id
                )
            )
            .scalars()
            .all()
        )
    for binding in bindings:
        parsed = json.loads(binding)
        assert parsed["output_kind"] != "RESULT"
        assert not any(ref in binding for ref in forbidden_refs)


def assert_merge_retry(
    h: Harness, before: Snapshot, probe: MergeProbe, result: dict[str, Any]
) -> None:
    assert_reached_merge_tail(probe)
    assessment = probe.assessments[0]
    assert result["semantic_merge"]["state"] == "AUTO_MERGED"
    assert result["semantic_merge"]["reason_codes"] == []
    assert result["head_mutated"] is True and probe.hits == 0
    assert h.runtime.ledger.read_heads(h.project)[h.key] == assessment.merged_revision_digest
    after = h.snapshot()
    assert_added_controls(before, after, probe.controls)
    for table, count in {
        "entity_snapshots": 1,
        "semantic_revisions": 1,
        "revision_parents": 2,
        "receipts": 1,
    }.items():
        assert set(before[table]).issubset(after[table])
        assert len(after[table]) == len(before[table]) + count
    refs = {"control:" + r.record_digest for r in probe.controls}
    refs.update(
        {
            "revision:" + str(assessment.merged_revision_digest),
            "receipt:" + probe.commits[0].receipt.receipt_digest,
        }
    )
    for table in ("resource_scope_heads", "resource_scope_history", "resource_scope_receipts"):
        assert set(before[table]).issubset(after[table])
        added = [json.loads(r) for r in set(after[table]) - set(before[table])]
        assert len(added) == len(refs) and {r["resource_ref"] for r in added} == refs
    changed = {
        "control_records",
        "entity_snapshots",
        "semantic_revisions",
        "revision_parents",
        "receipts",
        "working_heads",
        "project_head_sets",
        "resource_scope_heads",
        "resource_scope_history",
        "resource_scope_receipts",
    }
    for table in before:
        if table not in changed:
            assert before[table] == after[table], table
    assert any(r == probe.commits[0].receipt for r in h.runtime.ledger.read_receipts(h.project))
    check_reopened(h.runtime.ledger.engine, after)


def assert_reached_merge_tail(probe: MergeProbe) -> None:
    assert probe.order == [
        "canonical.commit:FAST_FORWARD",
        "semantic.merge:AUTO_MERGED",
        "control:MERGE_PROPOSAL:AUTO_MERGED",
        "baseline.refresh",
        "control:MERGE_RECEIPT:AUTO_MERGED",
    ]
    assert len(probe.assessments) == len(probe.commits) == 1
    assert probe.assessments[0].head_mutated is True
    assert probe.assessments[0].receipt_ref == probe.commits[0].receipt.receipt_id


def save(
    name: str, before: Snapshot, after: Snapshot, probe: MergeProbe, **details: object
) -> None:
    delta = {
        table: {
            "removed": [json.loads(row) for row in sorted(set(before[table]) - set(after[table]))],
            "added": [json.loads(row) for row in sorted(set(after[table]) - set(before[table]))],
        }
        for table in before
        if before[table] != after[table]
    }
    report = {
        "verdict": details.pop("verdict"),
        "changed_tables": list(delta),
        "delta": delta,
        "before_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "after_sha256": hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest(),
        "fault_hits": probe.hits,
        "write_order": probe.order,
        "original_receipt_write_observed": probe.raw_receipt_visible_before_fault,
        "assessment": probe.assessments[0].model_dump(mode="json"),
        "canonical_receipt": probe.commits[0].receipt.model_dump(mode="json"),
        "attempted_controls": [r.model_dump(mode="json") for r in probe.controls],
        **details,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"o3-o1-{name}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


async def test_auto_merge_success_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with harness(tmp_path) as h:
        body = await prepare_siblings(h)
        before = h.snapshot()
        with monkeypatch.context() as patch:
            probe = install(patch, h, fail=False)
            result = value(await h.call("revision/merge/propose", "o1-control", body))
        assert_reached_merge_tail(probe)
        assessment = probe.assessments[0]
        assert result["head_mutated"] is True
        assert h.runtime.ledger.read_heads(h.project)[h.key] == assessment.merged_revision_digest
        assert any(
            r.receipt_id == assessment.receipt_ref
            for r in h.runtime.ledger.read_receipts(h.project)
        )
        after = h.snapshot()
        stored = control_rows(after)
        assert all(r.control_revision_id in stored for r in probe.controls)
        check_reopened(h.runtime.ledger.engine, after)
        save("control", before, after, probe, verdict="CONTROL_PASS", model_calls=h.model.calls)


async def test_late_merge_receipt_fault_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path) as h:
        body = await prepare_siblings(h)
        before = h.snapshot()
        heads_before = dict(h.runtime.ledger.read_heads(h.project))
        with monkeypatch.context() as patch:
            probe = install(patch, h, fail=True)
            failed = await h.call("revision/merge/propose", "o1-fault", body)
            assert failed.error is not None and probe.hits == 1
            assert_reached_merge_tail(probe)
            after = h.snapshot()
            check_reopened(h.runtime.ledger.engine, after)
            order = list(probe.order)
            replay = await h.call("revision/merge/propose", "o1-fault", body)
            assert replay.error == failed.error and probe.order == order and probe.hits == 1
            assert h.snapshot() == after
        assessment = probe.assessments[0]
        assert assessment.merged_revision_digest is not None
        stored = control_rows(after)
        receipt_ids = {r.receipt_id for r in h.runtime.ledger.read_receipts(h.project)}
        remaining_controls = [
            r.model_dump(mode="json") for r in probe.controls if r.control_revision_id in stored
        ]
        verdict = "HOLD_PARTIAL_PUBLICATION" if after != before else "NO_PARTIAL_PUBLICATION"
        save(
            "fault",
            before,
            after,
            probe,
            verdict=verdict,
            response_error=failed.error.model_dump(mode="json"),
            heads_before=heads_before,
            heads_after=dict(h.runtime.ledger.read_heads(h.project)),
            merged_revision_present=h.runtime.ledger.read_revision_by_digest(
                h.project, assessment.merged_revision_digest
            )
            is not None,
            canonical_receipt_present=assessment.receipt_ref in receipt_ids,
            remaining_controls=remaining_controls,
            reopened_snapshot_equal=True,
            same_key_error_replay_equal=True,
            replay_additional_writes=0,
            model_calls=h.model.calls,
            observation_scope="D automatic merge rollback and replay",
        )
        assert h.model.calls == 0
        assert after == before, (
            "D_RED_PARTIAL_PUBLICATION: late merge receipt failure left committed domain state"
        )
        forbidden = ["control:" + r.record_digest for r in probe.controls]
        forbidden.extend(
            [
                "revision:" + assessment.merged_revision_digest,
                "receipt:" + probe.commits[0].receipt.receipt_digest,
            ]
        )
        assert_failed_operation(h, failed, "revision/merge/propose", "o1-fault", forbidden)
        with monkeypatch.context() as patch:
            retry_probe = install(patch, h, fail=False)
            retried = await h.call("revision/merge/propose", "o1-new-key", body)
            result = value(retried)
            assert_merge_retry(h, before, retry_probe, result)
            successful = h.snapshot()
            order = list(retry_probe.order)
            again = await h.call("revision/merge/propose", "o1-new-key", body)
            assert value(again) == result and retry_probe.order == order
            assert h.snapshot() == successful
        save(
            "atomic-retry",
            before,
            successful,
            retry_probe,
            verdict="PASS",
            same_key_success_replay=True,
        )
