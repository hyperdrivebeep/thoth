from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.adapters.storage import SqliteDependencyGraph
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.relation import DependencyRelation
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


async def propose(
    runtime: AppRuntime,
    *,
    project_id: str,
    object_id: str,
    parent: str,
    content: dict[str, JsonValue],
    suffix: str,
    schema_version: str = "1.0.0",
) -> dict[str, JsonValue]:
    result = value(
        await runtime.bus.dispatch(
            request(
                "revision/propose",
                f"a07-propose-{suffix}",
                {
                    "project_id": project_id,
                    "aggregate_id": object_id,
                    "aggregate_type": "DECISION_OBJECT",
                    "parent_revision_digests": [parent],
                    "candidate_content": content,
                    "reason": f"A07 {suffix} branch",
                    "evidence_refs": [],
                    "actor_or_agent_ref": f"agent:a07:{suffix}",
                    "expected_head_digest": parent,
                    "candidate_schema_version": schema_version,
                },
            )
        )
    )
    return cast(dict[str, JsonValue], result["proposal"])


async def commit(
    runtime: AppRuntime,
    *,
    project_id: str,
    expected_heads: dict[str, str],
    proposal_digest: str,
    suffix: str,
) -> dict[str, JsonValue]:
    created = value(
        await runtime.bus.dispatch(
            request(
                "revision/changeSet/create",
                f"a07-changeset-{suffix}",
                {
                    "project_id": project_id,
                    "expected_head_set": expected_heads,
                    "candidate_revision_digests": [proposal_digest],
                    "transition_reason": f"commit A07 {suffix} branch",
                    "impact_policy_ref": "merge:three-way-v1",
                },
            )
        )
    )
    change_set = cast(dict[str, JsonValue], created["change_set"])
    validated = value(
        await runtime.bus.dispatch(
            request(
                "revision/changeSet/validate",
                f"a07-validate-{suffix}",
                {
                    "project_id": project_id,
                    "record_id": change_set["record_id"],
                    "expected_change_set_revision": change_set["version"],
                },
            )
        )
    )
    assert validated["state"] == "READY_TO_COMMIT"
    return value(
        await runtime.bus.dispatch(
            request(
                "revision/changeSet/commit",
                f"a07-commit-{suffix}",
                {
                    "project_id": project_id,
                    "record_id": change_set["record_id"],
                    "validation_bundle_digest": validated["validation_bundle_digest"],
                    "expected_head_set_digest": head_set_digest(expected_heads),
                },
            )
        )
    )


@pytest.mark.asyncio
async def test_public_revision_flow_preserves_siblings_and_auto_merges_independent_paths(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    project_id = "project:a07:independent"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "a07-project",
                    {
                        "project_id": project_id,
                        "name": "A07 semantic merge",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "a07-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:a07:independent",
                        "problem": "Merge independent semantic edits",
                        "scope": {"workstream": "merge"},
                    },
                )
            )
        )
        object_id = str(cast(list[JsonValue], thread["current_object_ids"])[0])
        aggregate_key = f"DECISION_OBJECT:{object_id}"
        base_heads = dict(runtime.ledger.read_heads(project_id))
        base = base_heads[aggregate_key]
        revision = runtime.ledger.read_revision_by_digest(project_id, base)
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        left_content = cast(dict[str, JsonValue], snapshot.content) | {
            "purpose_statement": "left actor clarified purpose"
        }
        right_content = cast(dict[str, JsonValue], snapshot.content) | {
            "problem_frame": "right actor clarified problem frame"
        }
        left_proposal = await propose(
            runtime,
            project_id=project_id,
            object_id=object_id,
            parent=base,
            content=left_content,
            suffix="left",
        )
        right_proposal = await propose(
            runtime,
            project_id=project_id,
            object_id=object_id,
            parent=base,
            content=right_content,
            suffix="right",
        )
        left_commit = await commit(
            runtime,
            project_id=project_id,
            expected_heads=base_heads,
            proposal_digest=str(left_proposal["record_digest"]),
            suffix="left",
        )
        left_head = cast(dict[str, str], left_commit["new_project_head_set"])[aggregate_key]
        right_commit = await commit(
            runtime,
            project_id=project_id,
            expected_heads=base_heads,
            proposal_digest=str(right_proposal["record_digest"]),
            suffix="right",
        )
        right_branches = cast(list[str], right_commit["branch_revision_digests"])
        assert len(right_branches) == 1
        right_branch = right_branches[0]
        assert runtime.ledger.read_revision_by_digest(project_id, right_branch) is not None
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left_head

        merged_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-merge-independent",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left_head, right_branch],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "auto merge independent field paths",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], merged_result["semantic_merge"])
        assert merge["state"] == "AUTO_MERGED"
        assert merge["common_ancestor_digest"] == base
        assert merge["conflict_paths"] == []
        assert set(cast(list[str], merge["left_changed_paths"])) == {"/purpose_statement"}
        assert set(cast(list[str], merge["right_changed_paths"])) == {"/problem_frame"}
        assert merged_result["head_mutated"] is True
        merge_receipt = cast(dict[str, JsonValue], merged_result["receipt"])
        assert merge_receipt["semantic_truth"] == "NOT_CERTIFIED"
        assert merge_receipt["authorization"] == "SEPARATE"
        merged_digest = str(merge["merged_revision_digest"])
        merged_revision = runtime.ledger.read_revision_by_digest(project_id, merged_digest)
        assert merged_revision is not None
        assert set(merged_revision.parent_revision_digests) == {left_head, right_branch}
        merged_snapshot = runtime.ledger.read_snapshot(merged_revision.snapshot_id)
        assert merged_snapshot is not None
        assert merged_snapshot.content["purpose_statement"] == "left actor clarified purpose"
        assert merged_snapshot.content["problem_frame"] == "right actor clarified problem frame"

        dependent_ref = "HYPOTHESIS:a07:dependent"
        relation_draft: dict[str, object] = {
            "relation_id": "relation:a07:restore",
            "project_id": project_id,
            "source_ref": aggregate_key,
            "relation_type": "DERIVES",
            "target_ref": dependent_ref,
            "payload": {"reason": "restore recalculation acceptance"},
        }
        SqliteDependencyGraph(runtime.ledger.engine).add(
            DependencyRelation(
                relation_id="relation:a07:restore",
                project_id=project_id,
                source_ref=aggregate_key,
                relation_type="DERIVES",
                target_ref=dependent_ref,
                payload={"reason": "restore recalculation acceptance"},
                revision_digest=domain_digest(
                    "DEPENDENCY_RELATION",
                    "1.0.0",
                    canonical_payload(relation_draft),
                ),
            )
        )
        restored = value(
            await runtime.bus.dispatch(
                request(
                    "revision/restore",
                    "a07-restore-as-new",
                    {
                        "project_id": project_id,
                        "entity_type": "DECISION_OBJECT",
                        "entity_id": object_id,
                        "selected_revision_id": revision.revision_id,
                        "expected_current_head": merged_digest,
                        "reason": "restore base as a new revision and recalculate dependents",
                        "actor_id": "human:a07:owner",
                        "actor_role": "project-owner",
                    },
                )
            )
        )
        restore = cast(dict[str, JsonValue], restored["restore"])
        assert restore["restored_revision_id"] != revision.revision_id
        assert dependent_ref in cast(list[str], restore["recalculate_refs"])
        assert runtime.ledger.read_dependency_states(project_id)[dependent_ref] == (
            "RECALCULATION_REQUIRED"
        )
        revision_count = len(
            runtime.ledger.read_revisions(project_id, "DECISION_OBJECT", object_id)
        )
        stale_restore = await runtime.bus.dispatch(
            request(
                "revision/restore",
                "a07-stale-restore",
                {
                    "project_id": project_id,
                    "entity_type": "DECISION_OBJECT",
                    "entity_id": object_id,
                    "selected_revision_id": revision.revision_id,
                    "expected_current_head": merged_digest,
                    "reason": "stale restore must fail before write",
                    "actor_id": "human:a07:owner",
                    "actor_role": "project-owner",
                },
            )
        )
        assert stale_restore.error is not None
        assert "stale" in stale_restore.error.message
        assert len(runtime.ledger.read_revisions(project_id, "DECISION_OBJECT", object_id)) == (
            revision_count
        )
    finally:
        runtime.close()


async def prepare_conflicting_siblings(
    tmp_path: Path,
    *,
    suffix: str,
    left_updates: dict[str, JsonValue],
    right_updates: dict[str, JsonValue],
    left_schema: str = "1.0.0",
    right_schema: str = "1.0.0",
    base_updates: dict[str, JsonValue] | None = None,
) -> tuple[AppRuntime, str, str, str, str, str, str]:
    runtime = create_runtime(tmp_path / suffix)
    project_id = f"project:a07:{suffix}"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"a07-{suffix}-project",
                {
                    "project_id": project_id,
                    "name": f"A07 {suffix}",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
    )
    thread = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                f"a07-{suffix}-thread",
                {
                    "project_id": project_id,
                    "thread_id": f"thread:a07:{suffix}",
                    "problem": "Exercise typed semantic merge gates",
                    "scope": {"workstream": "merge"},
                },
            )
        )
    )
    object_id = str(cast(list[JsonValue], thread["current_object_ids"])[0])
    aggregate_key = f"DECISION_OBJECT:{object_id}"
    base_heads = dict(runtime.ledger.read_heads(project_id))
    base = base_heads[aggregate_key]
    base_revision = runtime.ledger.read_revision_by_digest(project_id, base)
    assert base_revision is not None
    snapshot = runtime.ledger.read_snapshot(base_revision.snapshot_id)
    assert snapshot is not None
    if base_updates:
        base_proposal = await propose(
            runtime,
            project_id=project_id,
            object_id=object_id,
            parent=base,
            content=cast(dict[str, JsonValue], snapshot.content) | base_updates,
            suffix=f"{suffix}-base",
        )
        base_result = await commit(
            runtime,
            project_id=project_id,
            expected_heads=base_heads,
            proposal_digest=str(base_proposal["record_digest"]),
            suffix=f"{suffix}-base",
        )
        base_heads = cast(dict[str, str], base_result["new_project_head_set"])
        base = base_heads[aggregate_key]
        base_revision = runtime.ledger.read_revision_by_digest(project_id, base)
        assert base_revision is not None
        snapshot = runtime.ledger.read_snapshot(base_revision.snapshot_id)
        assert snapshot is not None
    left = await propose(
        runtime,
        project_id=project_id,
        object_id=object_id,
        parent=base,
        content=cast(dict[str, JsonValue], snapshot.content) | left_updates,
        suffix=f"{suffix}-left",
        schema_version=left_schema,
    )
    right = await propose(
        runtime,
        project_id=project_id,
        object_id=object_id,
        parent=base,
        content=cast(dict[str, JsonValue], snapshot.content) | right_updates,
        suffix=f"{suffix}-right",
        schema_version=right_schema,
    )
    left_result = await commit(
        runtime,
        project_id=project_id,
        expected_heads=base_heads,
        proposal_digest=str(left["record_digest"]),
        suffix=f"{suffix}-left",
    )
    left_head = cast(dict[str, str], left_result["new_project_head_set"])[aggregate_key]
    right_result = await commit(
        runtime,
        project_id=project_id,
        expected_heads=base_heads,
        proposal_digest=str(right["record_digest"]),
        suffix=f"{suffix}-right",
    )
    right_head = cast(list[str], right_result["branch_revision_digests"])[0]
    return runtime, project_id, object_id, aggregate_key, base, left_head, right_head


@pytest.mark.asyncio
async def test_overlapping_field_changes_remain_open_conflict_without_head_mutation(
    tmp_path: Path,
) -> None:
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="overlap",
        left_updates={"purpose_statement": "left meaning"},
        right_updates={"purpose_statement": "right meaning"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-overlap-merge",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "overlap must remain open",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        assert merge["state"] == "OPEN_CONFLICT"
        assert merge["common_ancestor_digest"] == base
        assert merge["conflict_paths"] == ["/purpose_statement"]
        assert "OVERLAPPING_FIELD_CHANGE" in cast(list[str], merge["reason_codes"])
        assert result["head_mutated"] is False
        assert cast(dict[str, JsonValue], result["receipt"])["semantic_truth"] == ("NOT_CERTIFIED")
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left
        assert result["conflict"] is not None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_authority_cutoff_policy_dependency_and_domain_gates_fail_closed(
    tmp_path: Path,
) -> None:
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="protected",
        left_updates={
            "authority_state": "APPROVED",
            "cutoff_state": "ELIGIBLE",
            "policy_binding": "policy:changed",
            "dependency_refs": ["HYPOTHESIS:protected"],
            "criterion_state": "MET",
        },
        right_updates={"problem_frame": "safe independent change"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-protected-merge",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "protected paths require explicit resolution",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        gates = cast(dict[str, JsonValue], merge["gates"])
        assert merge["state"] == "OPEN_CONFLICT"
        assert gates["authority"] is False
        assert gates["cutoff"] is False
        assert gates["policy"] is False
        assert gates["dependency"] is False
        assert gates["domain"] is False
        assert result["head_mutated"] is False
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_domain_type_change_cannot_auto_merge_with_independent_branch(
    tmp_path: Path,
) -> None:
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="domain-type",
        left_updates={"purpose_statement": {"invalid": "shape change"}},
        right_updates={"problem_frame": "otherwise independent change"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-domain-merge",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "domain shape mismatch must hold",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        assert merge["state"] == "OPEN_CONFLICT"
        assert "/purpose_statement" in cast(list[str], merge["conflict_paths"])
        assert cast(dict[str, JsonValue], merge["gates"])["domain"] is False
        assert result["head_mutated"] is False
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "security_update",
    (
        {
            "risk_tier": "R1",
            "required_roles": ["configuration-owner"],
            "authorization_state": "APPROVED",
            "external_write": True,
        },
        {
            "alternatives": [
                {
                    "action_id": "action:a07:nested",
                    "risk_tier": "R1",
                    "effect_facts": {"external_write": True},
                    "required_roles": ["configuration-owner"],
                }
            ]
        },
    ),
)
async def test_action_security_fields_and_nested_subtrees_never_auto_merge(
    tmp_path: Path,
    security_update: dict[str, JsonValue],
) -> None:
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix=f"action-security-{len(security_update)}",
        left_updates=security_update,
        right_updates={"problem_frame": "safe independent sibling edit"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    f"a07-action-security-{len(security_update)}",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "Action security fields require explicit review",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        assert merge["state"] == "OPEN_CONFLICT"
        assert result["head_mutated"] is False
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left
        assert cast(list[str], merge["conflict_paths"])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_action_security_key_with_json_pointer_escape_remains_protected(
    tmp_path: Path,
) -> None:
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="action-security-escaped",
        left_updates={"action/plan": [{"risk_tier": "R3"}]},
        right_updates={"problem_frame": "safe sibling edit"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-action-security-escaped",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "escaped Action path remains protected",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        assert merge["state"] == "OPEN_CONFLICT"
        assert "/action~1plan" in cast(list[str], merge["conflict_paths"])
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unchanged_action_risk_inside_list_does_not_block_safe_list_edit(
    tmp_path: Path,
) -> None:
    base_alternatives: list[JsonValue] = [
        {"action_id": "action:a07:safe", "label": "before", "risk_tier": "R0"}
    ]
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="action-safe-list",
        base_updates={"alternatives": base_alternatives},
        left_updates={
            "alternatives": [{"action_id": "action:a07:safe", "label": "after", "risk_tier": "R0"}]
        },
        right_updates={"problem_frame": "safe sibling edit"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-action-safe-list",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "safe label edit may auto merge",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        assert merge["state"] == "AUTO_MERGED"
        assert (
            runtime.ledger.read_heads(project_id)[aggregate_key] == merge["merged_revision_digest"]
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_mixed_overlap_and_action_security_conflict_keeps_both_reason_codes(
    tmp_path: Path,
) -> None:
    (
        runtime,
        project_id,
        object_id,
        _aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="mixed-reasons",
        left_updates={"purpose_statement": "left", "risk_tier": "R2"},
        right_updates={"purpose_statement": "right"},
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-mixed-reasons",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "preserve all conflict reasons",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        reasons = cast(list[str], merge["reason_codes"])
        assert "OVERLAPPING_FIELD_CHANGE" in reasons
        assert "POLICY_REVIEW_REQUIRED" in reasons
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_schema_mismatch_remains_open_conflict(
    tmp_path: Path,
) -> None:
    (
        runtime,
        project_id,
        object_id,
        aggregate_key,
        _base,
        left,
        right,
    ) = await prepare_conflicting_siblings(
        tmp_path,
        suffix="schema",
        left_updates={"purpose_statement": "schema one branch"},
        right_updates={"problem_frame": "schema two branch"},
        right_schema="2.0.0",
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/merge/propose",
                    "a07-schema-merge",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "parent_revision_digests": [left, right],
                        "merge_policy_ref": "merge:three-way-v1",
                        "reason": "schema mismatch must remain open",
                        "evidence_refs": [],
                    },
                )
            )
        )
        merge = cast(dict[str, JsonValue], result["semantic_merge"])
        assert merge["state"] == "OPEN_CONFLICT"
        assert cast(dict[str, JsonValue], merge["gates"])["schema_compatible"] is False
        assert "SCHEMA_MISMATCH" in cast(list[str], merge["reason_codes"])
        assert result["head_mutated"] is False
        assert runtime.ledger.read_heads(project_id)[aggregate_key] == left
    finally:
        runtime.close()
