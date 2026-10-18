"""Pure publication construction; effect and transaction ownership stay in handlers."""

from __future__ import annotations

from collections.abc import Iterable

from thoth.application.commands.revisions_inputs import (
    BaselineDecideInput,
    ChangeSetCommitInput,
    MergeProposeInput,
    RestoreProposeInput,
)
from thoth.application.services.revision_service import CommitResult
from thoth.domain.actor import ActorRef
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import ActorKind
from thoth.domain.revision import (
    EntitySnapshot,
    SemanticMergeReceipt,
    SemanticMergeResult,
    SemanticRevision,
    StagedRevision,
)


def current_baseline_for(
    baselines: Iterable[ControlRecord], candidate: ControlRecord
) -> ControlRecord | None:
    """The CURRENT baseline that was already made from this candidate, if there is one."""
    return next(
        (
            item
            for item in baselines
            if item.state == "CURRENT"
            and item.payload.get("candidate_digest") == candidate.record_digest
        ),
        None,
    )


def domain_transition_names(entity_type: str, content: dict[str, object]) -> list[str]:
    domain_transitions: list[str] = []
    if entity_type == "ACTION" and content.get("proposal_state") == "RETIRED":
        domain_transitions.append("action/retired")
    if entity_type == "HYPOTHESIS" and content.get("development_stage") == "RETIRED":
        domain_transitions.append("hypothesis/retired")
    if entity_type == "DECISION_OBJECT":
        lifecycle = content.get("lifecycle")
        if lifecycle == "COMPLETED":
            domain_transitions.append("object/completed")
        if lifecycle == "CLOSED":
            domain_transitions.append("object/closed")
        if lifecycle == "SUPERSEDED":
            domain_transitions.append("object/superseded")
        if content.get("disposition") not in {None, "NONE"}:
            domain_transitions.append("object/dispositionChanged")
    if entity_type == "MEMORY":
        if content.get("pipeline_status") == "COMMITTED":
            domain_transitions.append("memory/committed")
        if content.get("support_status") == "CONFLICTING":
            domain_transitions.append("memory/conflictDetected")
    return domain_transitions


def commit_actor(
    authenticated: AuthenticatedActorContext | None, request: ChangeSetCommitInput
) -> ActorRef:
    return ActorRef(
        actor_id=(
            "agent:revision-coordinator" if authenticated is None else authenticated.actor_id
        ),
        kind=ActorKind.AGENT if authenticated is None else ActorKind.HUMAN,
        role="revision-coordinator" if authenticated is None else authenticated.role,
        project_id=request.project_id,
        session_id=None if authenticated is None else authenticated.session_id,
        role_assignment_ref=(None if authenticated is None else authenticated.role_assignment_id),
    )


def commit_outcome(
    result: CommitResult, staged: list[StagedRevision]
) -> tuple[str, tuple[str, ...]]:
    state = (
        "COMMITTED"
        if result.committed_revision_ids
        else "BRANCHED"
        if result.branch_revision_ids
        else "ABORTED"
    )
    branch_revision_ids = set(result.branch_revision_ids)
    branch_revision_digests = tuple(
        item.revision.revision_digest
        for item in staged
        if item.revision.revision_id in branch_revision_ids
    )
    return state, branch_revision_digests


def merge_proposal_payload(
    request: MergeProposeInput, assessment: SemanticMergeResult, conflicts: list[str]
) -> dict[str, object]:
    return {
        "aggregate_id": request.aggregate_id,
        "parent_revision_digests": request.parent_revision_digests,
        "candidate_content": request.candidate_content,
        "merge_policy_ref": request.merge_policy_ref,
        "reason": request.reason,
        "evidence_refs": request.evidence_refs,
        "conflicts": conflicts,
        "common_ancestor_digest": assessment.common_ancestor_digest,
        "semantic_merge": assessment.model_dump(mode="json"),
        "head_mutated": assessment.head_mutated,
    }


def merge_receipt_fields(
    request: MergeProposeInput, assessment: SemanticMergeResult
) -> dict[str, object]:
    return {
        "project_id": request.project_id,
        "merge_id": assessment.merge_id,
        "state": assessment.state.value,
        "parent_revision_digests": request.parent_revision_digests,
        "common_ancestor_digest": assessment.common_ancestor_digest,
        "conflict_paths": assessment.conflict_paths,
        "gates": assessment.gates,
        "integrity": "VALID",
        "provenance": "CANONICAL_REVISION_DAG",
        "authorization": "SEPARATE",
        "semantic_truth": "NOT_CERTIFIED",
    }


def seal_merge_receipt(receipt_draft: dict[str, object]) -> SemanticMergeReceipt:
    return SemanticMergeReceipt.model_validate(
        {
            **receipt_draft,
            "receipt_digest": domain_digest(
                "SEMANTIC_MERGE_RECEIPT",
                "1.0.0",
                canonical_payload(receipt_draft),
            ),
        }
    )


def resolved_change_fields(revision: ControlRecord) -> dict[str, object]:
    return {
        "candidate_revision_digests": (revision.record_digest,),
        "transition_reason": "resolved merge",
        "impact_policy_ref": "merge:default",
        "missing_dependencies": (),
        "conflict_state": "RESOLVED",
    }


def restore_proposal_fields(
    request: RestoreProposeInput,
    current: SemanticRevision,
    target_snapshot: EntitySnapshot,
    content: dict[str, object],
) -> dict[str, object]:
    return {
        "aggregate_id": request.aggregate_id,
        "aggregate_type": current.entity_type.value,
        "parent_revision_digests": (request.current_head_digest,),
        "candidate_content": content,
        "content_digest": target_snapshot.content_digest,
        "reason": request.reason,
        "evidence_refs": request.evidence_refs,
    }


def legacy_baseline_payload(
    candidate: ControlRecord, request: BaselineDecideInput
) -> dict[str, object]:
    return {
        **candidate.payload,
        "authority": {
            "actor_ref": request.actor_ref,
            "role_assignment_ref": request.role_assignment_ref,
        },
        "candidate_digest": candidate.record_digest,
    }


def staged_actor(actor_ref: str, payload: dict[str, object], proposal: ControlRecord) -> ActorRef:
    return ActorRef(
        actor_id=actor_ref,
        kind=ActorKind.AGENT if actor_ref.startswith("agent:") else ActorKind.HUMAN,
        role=str(payload.get("authenticated_role") or "revision-proposer"),
        project_id=proposal.project_id,
        session_id=(
            None
            if payload.get("authenticated_session_id") is None
            else str(payload.get("authenticated_session_id"))
        ),
        role_assignment_ref=(
            None
            if payload.get("authenticated_role_assignment_ref") is None
            else str(payload.get("authenticated_role_assignment_ref"))
        ),
    )
