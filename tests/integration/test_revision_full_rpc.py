from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.atomicity.harness import AllowedRow, Snapshot, assert_phase_delta
from tests.atomicity.harness import snapshot as database_snapshot

from thoth.apps.runtime import create_runtime
from thoth.domain.restore import RestoreProposalV1
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


def assert_restore_proposal_delta(
    before: Snapshot,
    after: Snapshot,
    response: JsonRpcResponse,
    project_id: str,
    current_head: str,
    target_head: str,
) -> None:
    proposed = value(response)
    proposal = proposed["restore_proposal"]
    change_set = proposed["change_set"]
    assert isinstance(proposal, dict) and isinstance(change_set, dict)
    assert proposal["state"] == "DRAFT" and change_set["state"] == "STAGED"
    payload = proposal["payload"]
    assert isinstance(payload, dict)
    typed_proposal = RestoreProposalV1.model_validate(payload)
    assert typed_proposal.selection.expected_current_head == current_head
    assert typed_proposal.restores_revision_digest == target_head
    assert typed_proposal.profile_id == "decision-object-record.v1"
    assert typed_proposal.parent_revision_digests == (current_head, target_head)
    assert isinstance(change_set["payload"], dict)
    assert change_set["payload"]["candidate_revision_digests"] == [proposal["record_digest"]]
    expected_heads = change_set["payload"]["expected_head_set"]
    assert isinstance(expected_heads, dict)
    assert expected_heads[f"DECISION_OBJECT:{typed_proposal.selection.entity_id}"] == current_head
    assert response.result is not None
    operation_id = response.result["operation_id"]
    identity = {"operation_id": operation_id}
    rules = [
        AllowedRow(
            "operations",
            {
                **identity,
                "project_id": project_id,
                "method": "revision/restore/propose",
                "idempotency_key": "revision-restore-propose",
            },
            "added",
            {"state": ("SUCCEEDED",), "error_json": (None,)},
        ),
        AllowedRow(
            "idempotency_keys",
            {
                **identity,
                "project_id": project_id,
                "method": "revision/restore/propose",
                "idempotency_key": "revision-restore-propose",
            },
            "added",
            {},
        ),
        AllowedRow(
            "events",
            {**identity, "project_id": project_id},
            "added",
            {
                "event_type": (
                    "operation.started",
                    "operation.succeeded",
                    "revision/restoreProposed",
                )
            },
        ),
        AllowedRow("checkpoints", identity, "added", {}),
        AllowedRow(
            "operation_resource_bindings", {**identity, "project_id": project_id}, "added", {}
        ),
    ]
    for record in (proposal, change_set):
        rules.append(
            AllowedRow(
                "control_records",
                {
                    "project_id": project_id,
                    "namespace": "REVISION",
                    "record_id": record["record_id"],
                    "record_digest": record["record_digest"],
                    "state": record["state"],
                    "version": 1,
                },
                "added",
                {},
            )
        )
        for table in ("resource_scope_heads", "resource_scope_history", "resource_scope_receipts"):
            rules.append(
                AllowedRow(
                    table,
                    {
                        "project_id": project_id,
                        "resource_ref": f"control:{record['record_digest']}",
                    },
                    "added",
                    {},
                )
            )
    assert_phase_delta(before, after, tuple(rules))
    expected_counts = {
        "operations": 1,
        "idempotency_keys": 1,
        "events": 3,
        "checkpoints": 1,
        "operation_resource_bindings": 1,
        "control_records": 2,
        "resource_scope_heads": 2,
        "resource_scope_history": 2,
        "resource_scope_receipts": 2,
    }
    for table, count in expected_counts.items():
        rows = [json.loads(row) for row in set(after[table]) - set(before[table])]
        assert len(rows) == count
        if table == "operation_resource_bindings":
            binding = json.loads(rows[0]["content_json"])
            assert binding["output_kind"] == "RESULT"
            assert sorted(
                (use["resource_ref"], use["capability"]) for use in binding["resource_uses"]
            ) == sorted([(f"revision:{current_head}", "READ"), (f"revision:{target_head}", "READ")])
        if table == "checkpoints":
            assert json.loads(rows[0]["payload_json"])["state"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_revision_atomic_changeset_merge_restore_and_baseline(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = create_runtime(workspace)
    project_id = "project:revision-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "revision-project",
                    {
                        "project_id": project_id,
                        "name": "Revision full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        role_result = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "revision-baseline-role",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "actor_id": "human:baseline-owner",
                        "role": "baseline-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["BASELINE_OWNER"],
                    },
                )
            )
        )
        role_id = str(cast(dict[str, JsonValue], role_result["role"])["role_assignment_id"])
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "revision-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:revision-full",
                        "problem": "Revise this decision object safely",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], thread["current_object_ids"])[0])
        head_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/head/read",
                    "revision-head-before",
                    {"project_id": project_id},
                )
            )
        )
        heads_before = cast(dict[str, str], head_result["working_heads"])
        aggregate_key = f"DECISION_OBJECT:{object_id}"
        old_head = heads_before[aggregate_key]
        old_revision = value(
            await runtime.bus.dispatch(
                request(
                    "revision/read",
                    "revision-read-old",
                    {"project_id": project_id, "revision_digest": old_head},
                )
            )
        )
        content_digest = str(old_revision["content_digest"])
        content_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/content/read",
                    "revision-content-old",
                    {"project_id": project_id, "content_digest": content_digest},
                )
            )
        )
        snapshot = cast(dict[str, JsonValue], content_result["snapshot"])
        candidate_content = cast(dict[str, JsonValue], snapshot["content"])
        candidate_content = {
            **candidate_content,
            "purpose_statement": "Revised decision purpose with immutable history",
        }
        proposal_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/propose",
                    "revision-propose",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "aggregate_type": "DECISION_OBJECT",
                        "parent_revision_digests": [old_head],
                        "candidate_content": candidate_content,
                        "reason": "exercise atomic semantic revision",
                        "evidence_refs": [],
                        "actor_or_agent_ref": "agent:revision-test",
                        "expected_head_digest": old_head,
                    },
                )
            )
        )
        proposal = cast(dict[str, JsonValue], proposal_result["proposal"])
        change_set_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/changeSet/create",
                    "revision-changeset-create",
                    {
                        "project_id": project_id,
                        "expected_head_set": heads_before,
                        "candidate_revision_digests": [proposal["record_digest"]],
                        "transition_reason": "atomic object revision",
                        "impact_policy_ref": "impact:default",
                    },
                )
            )
        )
        change_set = cast(dict[str, JsonValue], change_set_result["change_set"])
        change_set_id = str(change_set["record_id"])
        validated_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/changeSet/validate",
                    "revision-changeset-validate",
                    {
                        "project_id": project_id,
                        "record_id": change_set_id,
                        "expected_change_set_revision": change_set["version"],
                    },
                )
            )
        )
        assert validated_result["state"] == "READY_TO_COMMIT"
        committed_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/changeSet/commit",
                    "revision-changeset-commit",
                    {
                        "project_id": project_id,
                        "record_id": change_set_id,
                        "validation_bundle_digest": validated_result["validation_bundle_digest"],
                        "expected_head_set_digest": head_result["project_head_set_digest"],
                    },
                )
            )
        )
        assert committed_result["atomic_commit"] is True
        heads_after = cast(dict[str, str], committed_result["new_project_head_set"])
        new_head = heads_after[aggregate_key]
        assert new_head != old_head

        diff_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/diff/read",
                    "revision-diff",
                    {
                        "project_id": project_id,
                        "from_revision_digest": old_head,
                        "to_revision_digest": new_head,
                    },
                )
            )
        )
        assert "/purpose_statement" in cast(list[str], diff_result["affected_paths"])
        graph = value(
            await runtime.bus.dispatch(
                request(
                    "revision/graph/read",
                    "revision-graph",
                    {"project_id": project_id, "aggregate_id": object_id},
                )
            )
        )
        assert len(cast(list[object], graph["nodes"])) >= 2
        branch = value(
            await runtime.bus.dispatch(
                request(
                    "revision/branch/create",
                    "revision-branch",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "from_revision_digest": old_head,
                        "purpose": "explore alternative without moving canonical head",
                        "actor_or_agent_ref": "agent:revision-test",
                    },
                )
            )
        )
        assert branch["content_mutated"] is False

        merge_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "revision-merge-propose",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [old_head, new_head],
                        "merge_policy_ref": "merge:nonoverlap-only",
                        "reason": "detect semantic conflicts",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], merge_result["merge_proposal"])
        conflict = cast(dict[str, JsonValue], merge_result["conflict"])
        assert merge["state"] == "OPEN_CONFLICT"
        conflict_id = str(conflict["record_id"])
        resolved_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/resolve",
                    "revision-merge-resolve",
                    {
                        "project_id": project_id,
                        "merge_proposal_id": merge["record_id"],
                        "resolved_content": candidate_content,
                        "resolution_map": {"purpose_statement": "use revised purpose"},
                        "evidence_refs": [],
                        "actor_or_agent_ref": "human:baseline-owner",
                        "expected_conflict_revision": conflict["version"],
                    },
                )
            )
        )
        assert resolved_result["head_mutated"] is False

        restore_preview = value(
            await runtime.bus.dispatch(
                request(
                    "revision/restore/preview",
                    "revision-restore-preview",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "target_revision_digest": old_head,
                        "current_head_digest": new_head,
                    },
                )
            )
        )
        assert restore_preview["protected_boundary_warning"]
        restore_command = request(
            "revision/restore/propose",
            "revision-restore-propose",
            {
                "project_id": project_id,
                "aggregate_id": object_id,
                "current_head_digest": new_head,
                "target_revision_digest": old_head,
                "reason": "restore content as a new revision",
                "evidence_refs": [],
                "actor_or_agent_ref": "human:baseline-owner",
            },
        )
        before_restore = database_snapshot(runtime.ledger.engine)
        before_heads = dict(runtime.ledger.read_heads(project_id))
        restore_proposal = await runtime.bus.dispatch(restore_command)
        assert dict(runtime.ledger.read_heads(project_id)) == before_heads
        assert_restore_proposal_delta(
            before_restore,
            database_snapshot(runtime.ledger.engine),
            restore_proposal,
            project_id,
            new_head,
            old_head,
        )
        recompute = value(
            await runtime.bus.dispatch(
                request(
                    "revision/recompute/request",
                    "revision-recompute",
                    {
                        "project_id": project_id,
                        "change_set_id": change_set_id,
                        "projection_refs": [
                            f"HYPOTHESIS:{object_id}",
                            f"ACTION:{object_id}",
                        ],
                        "recompute_policy_ref": "recompute:bounded-v1",
                    },
                )
            )
        )
        assert isinstance(recompute["recompute_task"], dict)

        baseline_prepare = value(
            await runtime.bus.dispatch(
                request(
                    "revision/baseline/prepare",
                    "revision-baseline-prepare",
                    {
                        "project_id": project_id,
                        "purpose": "seal integration milestone",
                        "scoped_head_map": {aggregate_key: new_head},
                        "policy_version": "baseline:1",
                        "evidence_refs": [],
                    },
                )
            )
        )
        candidate = cast(dict[str, JsonValue], baseline_prepare["baseline_candidate"])
        baseline_decide = value(
            await runtime.bus.dispatch(
                request(
                    "revision/baseline/decide",
                    "revision-baseline-decide",
                    {
                        "project_id": project_id,
                        "baseline_candidate_id": candidate["record_id"],
                        "decision": "APPROVE",
                        "actor_ref": "human:baseline-owner",
                        "role_assignment_ref": role_id,
                        "approved_digest": candidate["record_digest"],
                    },
                )
            )
        )
        baseline = cast(dict[str, JsonValue], baseline_decide["baseline"])
        baseline_digest = str(baseline["record_digest"])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/baseline/read",
                        "revision-baseline-read",
                        {
                            "project_id": project_id,
                            "baseline_set_digest": baseline_digest,
                        },
                    )
                )
            )["baseline"],
            dict,
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/baseline/list",
                        "revision-baseline-list",
                        {"project_id": project_id},
                    )
                )
            )["baselines"],
        )

        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/conflict/list",
                        "revision-conflict-list",
                        {"project_id": project_id},
                    )
                )
            )["conflicts"],
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/conflict/read",
                        "revision-conflict-read",
                        {"project_id": project_id, "record_id": conflict_id},
                    )
                )
            )["conflict"],
            dict,
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/changeSet/read",
                        "revision-changeset-read",
                        {"project_id": project_id, "record_id": change_set_id},
                    )
                )
            )["change_set"],
            dict,
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/list",
                        "revision-list",
                        {"project_id": project_id, "aggregate_id": object_id},
                    )
                )
            )["revisions"],
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "revision/impact/read",
                        "revision-impact",
                        {
                            "project_id": project_id,
                            "change_set_id": change_set_id,
                            "revision_digest": new_head,
                        },
                    )
                )
            )["change_set"],
            dict,
        )
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "revision/audit/read",
                    "revision-audit",
                    {"project_id": project_id, "aggregate_id": object_id},
                )
            )
        )
        assert cast(list[object], audit["revisions"])
        assert cast(list[object], audit["control_records"])
    finally:
        runtime.close()
