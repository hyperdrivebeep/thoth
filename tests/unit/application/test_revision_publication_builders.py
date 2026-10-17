"""Direct pure-builder contracts, including local transition order and ABORTED boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue
from tests.unit.application.test_revision_command_builders import record

from thoth.application.commands import revision_publication_records as records
from thoth.application.commands import revision_publication_views as views
from thoth.application.commands.revisions_inputs import (
    BaselineDecideInput,
    ChangeSetCommitInput,
    MergeProposeInput,
    RestoreProposeInput,
)
from thoth.application.services.revision_service import CommitResult
from thoth.domain.actor import ActorRef
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.baseline import BaselineDecision, BaselineSet
from thoth.domain.canonical import canonical_payload
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    SemanticMergeReceipt,
    SemanticMergeResult,
    SemanticRevision,
    StagedRevision,
)
from thoth.protocol.notifications import notifications_for

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures/revision_publication_golden.json"


def observed(scenario: str, key: str) -> dict[str, Any]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [value for _label, value in data[scenario]["outputs"] if key in value][-1]


def staged(number: int) -> StagedRevision:
    snapshot = EntitySnapshot(
        snapshot_id=f"snapshot:{number}",
        project_id="project:unit",
        entity_type=EntityType.ACTION,
        entity_id=f"entity:{number}",
        schema_version="1.0.0",
        content={"n": number},
        content_digest=str(number) * 64,
    )
    revision = SemanticRevision(
        revision_id=f"revision:{number}",
        project_id=snapshot.project_id,
        entity_type=snapshot.entity_type,
        entity_id=snapshot.entity_id,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=("a" * 64,),
        actor=ActorRef(
            actor_id="agent:unit", kind=ActorKind.AGENT, role="unit", project_id=snapshot.project_id
        ),
        reason="unit",
        evidence_refs=(),
        affected_refs=(),
        created_at=datetime(2026, 10, 16, tzinfo=UTC),
        schema_version="1.0.0",
        revision_digest=str(number + 2) * 64,
    )
    return StagedRevision(snapshot=snapshot, revision=revision)


@pytest.mark.parametrize(
    "entity,content,expected",
    [
        ("ACTION", {"proposal_state": "RETIRED"}, ["action/retired"]),
        ("HYPOTHESIS", {"development_stage": "RETIRED"}, ["hypothesis/retired"]),
        ("DECISION_OBJECT", {"lifecycle": "COMPLETED"}, ["object/completed"]),
        (
            "DECISION_OBJECT",
            {"lifecycle": "CLOSED", "disposition": "ARCHIVED"},
            ["object/closed", "object/dispositionChanged"],
        ),
        (
            "DECISION_OBJECT",
            {"lifecycle": "SUPERSEDED", "disposition": "NONE"},
            ["object/superseded"],
        ),
        (
            "MEMORY",
            {"pipeline_status": "COMMITTED", "support_status": "CONFLICTING"},
            ["memory/committed", "memory/conflictDetected"],
        ),
        ("ACTION", {"proposal_state": "ACTIVE"}, []),
        ("OTHER", {"lifecycle": "CLOSED"}, []),
    ],
)
def test_transition_order_and_input_unchanged(
    entity: str, content: dict[str, object], expected: list[str]
) -> None:
    before = dict(content)
    assert records.domain_transition_names(entity, content) == expected
    assert content == before


def test_transitions_keep_duplicates_until_caller_dedup_and_notifications() -> None:
    one = records.domain_transition_names(
        "DECISION_OBJECT", {"lifecycle": "CLOSED", "disposition": "ARCHIVED"}
    )
    both = [
        *one,
        *one,
        *records.domain_transition_names(
            "MEMORY", {"pipeline_status": "COMMITTED", "support_status": "CONFLICTING"}
        ),
    ]
    assert both[:4] == ["object/closed", "object/dispositionChanged"] * 2
    unique = list(dict.fromkeys(both))
    assert unique == [
        "object/closed",
        "object/dispositionChanged",
        "memory/committed",
        "memory/conflictDetected",
    ]
    notices = notifications_for(
        "revision/changeSet/commit",
        cast(dict[str, JsonValue], {"atomic_commit": True, "domain_transitions": unique}),
    )
    assert [name for name in notices if name in unique] == unique


@pytest.mark.parametrize("authenticated", [False, True])
def test_commit_actor_uses_supplied_context_only(authenticated: bool) -> None:
    context = (
        AuthenticatedActorContext(
            actor_id="human:unit",
            session_id="session:unit",
            project_id="project:unit",
            role_assignment_id="role:unit",
            role="revision-writer",
            capabilities=("READ", "WRITE"),
            data_scopes=("PROJECT",),
        )
        if authenticated
        else None
    )
    request = ChangeSetCommitInput(
        project_id="project:unit",
        record_id="change",
        validation_bundle_digest="a" * 64,
        expected_head_set_digest="b" * 64,
    )
    actor = records.commit_actor(context, request)
    assert actor.project_id == request.project_id
    assert actor.actor_id == ("human:unit" if authenticated else "agent:revision-coordinator")
    assert actor.session_id == ("session:unit" if authenticated else None)
    assert actor.role_assignment_ref == ("role:unit" if authenticated else None)
    assert actor.kind == (ActorKind.HUMAN if authenticated else ActorKind.AGENT)


@pytest.mark.parametrize(
    "actor_ref,payload,role,session",
    [
        ("agent:unit", {}, "revision-proposer", None),
        (
            "human:unit",
            {
                "authenticated_role": "owner",
                "authenticated_session_id": 123,
                "authenticated_role_assignment_ref": 456,
            },
            "owner",
            "123",
        ),
    ],
)
def test_staged_actor_keeps_fallback_and_string_conversion(
    actor_ref: str, payload: dict[str, object], role: str, session: str | None
) -> None:
    actor = records.staged_actor(actor_ref, payload, record(payload))
    assert actor.role == role and actor.session_id == session
    assert actor.kind == (ActorKind.AGENT if actor_ref.startswith("agent:") else ActorKind.HUMAN)
    assert actor.role_assignment_ref == (None if session is None else "456")


@pytest.mark.parametrize(
    "committed,branches,state",
    [
        (("revision:1",), (), "COMMITTED"),
        ((), ("revision:2", "revision:1"), "BRANCHED"),
        ((), (), "ABORTED"),
        (("revision:1",), ("revision:2",), "COMMITTED"),
    ],
)
def test_outcome_precedence_and_staged_order(
    committed: tuple[str, ...], branches: tuple[str, ...], state: str
) -> None:
    original = observed("generic_fast_forward", "change_set")["change_set"]["payload"][
        "commit_result"
    ]
    result = CommitResult.model_validate(
        {**original, "committed_revision_ids": committed, "branch_revision_ids": branches}
    )
    entries = [staged(1), staged(2)]
    before = [entry.model_dump_json() for entry in entries]
    actual, digests = records.commit_outcome(result, entries)
    assert actual == state
    assert digests == tuple(
        entry.revision.revision_digest
        for entry in entries
        if entry.revision.revision_id in branches
    )
    assert [entry.model_dump_json() for entry in entries] == before


@pytest.mark.parametrize("scenario", ["auto_merge", "conflict_resolve"])
def test_merge_payload_receipt_seal_and_response(scenario: str) -> None:
    source = observed(scenario, "semantic_merge")
    control = ControlRecord.model_validate(source["merge_proposal"])
    request = MergeProposeInput.model_validate(
        {
            "project_id": control.project_id,
            **{
                key: control.payload[key]
                for key in [
                    "aggregate_id",
                    "parent_revision_digests",
                    "candidate_content",
                    "merge_policy_ref",
                    "reason",
                    "evidence_refs",
                ]
            },
        }
    )
    assessment = SemanticMergeResult.model_validate(source["semantic_merge"])
    receipt = SemanticMergeReceipt.model_validate(source["receipt"])
    payload = records.merge_proposal_payload(request, assessment, list(assessment.conflict_paths))
    assert canonical_payload(payload) == canonical_payload(control.payload)
    fields = records.merge_receipt_fields(request, assessment)
    assert "receipt_id" not in fields and "recorded_at" not in fields
    before = canonical_payload(fields)
    sealed = records.seal_merge_receipt(
        {"receipt_id": receipt.receipt_id, **fields, "recorded_at": receipt.recorded_at}
    )
    assert sealed == receipt and canonical_payload(fields) == before
    conflict = (
        None if source["conflict"] is None else ControlRecord.model_validate(source["conflict"])
    )
    assert views.merge_response(control, conflict, assessment, sealed) == source


def test_resolved_change_and_view() -> None:
    result = observed("conflict_resolve", "candidate_revision")
    revision = ControlRecord.model_validate(result["candidate_revision"])
    resolved = ControlRecord.model_validate(result["merge_proposal"])
    change = ControlRecord.model_validate(result["change_set"])
    fields = records.resolved_change_fields(revision)
    assert fields["candidate_revision_digests"] == (revision.record_digest,)
    assert fields["conflict_state"] == "RESOLVED" and "expected_head_set" not in fields
    assert views.resolved_response(resolved, revision, change) == result


@pytest.mark.parametrize("scenario", ["typed_approve", "typed_reject"])
def test_typed_baseline_optional_set(scenario: str) -> None:
    result = observed(scenario, "typed_baseline_decision")
    decision = BaselineDecision.model_validate(result["typed_baseline_decision"])
    baseline = (
        None
        if result["typed_baseline_set"] is None
        else BaselineSet.model_validate(result["typed_baseline_set"])
    )
    assert views.typed_baseline_response(decision, baseline) == result


def test_legacy_baseline_draft_does_not_mutate_candidate() -> None:
    candidate = record({"scoped_head_map": {"ACTION:a": "a" * 64}})
    request = BaselineDecideInput(
        project_id=candidate.project_id,
        baseline_candidate_id=candidate.record_id,
        decision="APPROVE",
        actor_ref="human:unit",
        role_assignment_ref="role:unit",
        approved_digest=candidate.record_digest,
    )
    before = candidate.model_dump_json()
    payload = records.legacy_baseline_payload(candidate, request)
    assert payload["authority"] == {"actor_ref": "human:unit", "role_assignment_ref": "role:unit"}
    assert (
        payload["candidate_digest"] == candidate.record_digest
        and candidate.model_dump_json() == before
    )
    baseline = record(payload)
    assert views.baseline_response(baseline)["immutable_manifest"] == payload["scoped_head_map"]


def test_content_and_legacy_restore_are_pure_compatibility_values() -> None:
    current, target = staged(1), staged(2)
    request = RestoreProposeInput(
        project_id=current.revision.project_id,
        aggregate_id=current.revision.entity_id,
        current_head_digest=current.revision.revision_digest,
        target_revision_digest=target.revision.revision_digest,
        reason="compatibility",
        evidence_refs=(),
        actor_or_agent_ref="agent:unit",
    )
    fields = records.restore_proposal_fields(
        request, current.revision, target.snapshot, target.snapshot.content
    )
    assert fields["candidate_content"] == target.snapshot.content
    assert fields["content_digest"] == target.snapshot.content_digest
    assert "actor_or_agent_ref" not in fields and "authenticated_session_id" not in fields
    view = views.restore_preview_response(
        target.snapshot, current.revision, target.revision, ("downstream",)
    )
    assert view["new_parent"] == current.revision.revision_digest and view["dependent_impact"] == [
        "downstream"
    ]
    assert views.content_response(target.snapshot, target.revision)["lineage_refs"] == [
        target.revision.revision_digest
    ]
