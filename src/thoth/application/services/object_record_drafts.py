"""Object record drafts; guards, validation, transactions and publication remain in the service."""

from __future__ import annotations

from datetime import datetime

from thoth.domain.decision_object_full import (
    DecisionObjectRecord,
    ObjectAttentionRecord,
    ObjectCandidateRecord,
    ObjectRelationRecord,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def object_candidate_draft(
    *,
    project_id: str,
    thread_id: str,
    purpose_statement: str,
    problem_frame: str,
    focus_refs: tuple[str, ...],
    trigger_type: str,
    trigger_evidence_refs: tuple[str, ...],
    selected_refs: tuple[str, ...],
    profile_state: str,
    duplicates: tuple[str, ...],
    entry_validation: str,
    transition: str,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    return {
        "candidate_id": ids.new("object-candidate"),
        "project_id": project_id,
        "thread_id": thread_id,
        "purpose_statement": purpose_statement,
        "problem_frame": problem_frame,
        "focus_refs": focus_refs,
        "trigger_type": trigger_type,
        "trigger_evidence_refs": trigger_evidence_refs,
        "profile_candidates": selected_refs,
        "profile_state": profile_state,
        "duplicate_object_refs": duplicates,
        "entry_validation": entry_validation,
        "permitted_transition": transition,
        "candidate_state": "PROPOSED",
        "created_at": clock.now(),
    }


def materialized_object_draft(
    *,
    project_id: str,
    thread_id: str,
    parent_object_id: str | None,
    purpose_statement: str,
    problem_frame: str,
    focus_refs: tuple[str, ...],
    workstream_refs: tuple[str, ...],
    requirement_refs: tuple[str, ...],
    selected_refs: tuple[str, ...],
    profile_state: str,
    trigger_type: str,
    trigger_evidence_refs: tuple[str, ...],
    actor_ref: str,
    object_id: str | None,
    candidate: ObjectCandidateRecord,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    return {
        "object_revision_id": ids.new("object-revision"),
        "object_id": object_id or ids.new("object"),
        "project_id": project_id,
        "thread_id": thread_id,
        "parent_object_id": parent_object_id,
        "purpose_statement": purpose_statement,
        "problem_frame": problem_frame,
        "focus_refs": focus_refs,
        "workstream_refs": workstream_refs,
        "requirement_refs": requirement_refs,
        "profile_refs": selected_refs,
        "profile_state": profile_state,
        "facets": (),
        "materialization_trigger": trigger_type,
        "trigger_evidence_refs": trigger_evidence_refs,
        "entry_criteria": {"status": "PASS", "candidate_ref": candidate.candidate_id},
        "exit_criteria": {"status": "NOT_SATISFIED", "reason": "work remains open"},
        "active_work_mode": "FRAMING",
        "lifecycle": "ACTIVE",
        "resolution": "OPEN",
        "verification": "NOT_ASSESSED",
        "disposition": "NONE",
        "freshness": "CURRENT",
        "attention": "NONE",
        "blockers": (),
        "relation_refs": (),
        "actor_or_agent_ref": actor_ref,
        "protected_boundary_context": {},
        "created_at": clock.now(),
    }


def revised_object_draft(
    current: DecisionObjectRecord,
    updates: dict[str, object],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "object_revision_id": ids.new("object-revision"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    draft.pop("receipt_ref", None)
    return draft


def object_relation_draft(
    current: DecisionObjectRecord,
    *,
    relation_type: str,
    target_ref: str,
    semantic_role: str,
    evidence_refs: tuple[str, ...],
    authority_state: str,
    actor_ref: str,
    now: datetime,
    ids: IdGeneratorPort,
) -> dict[str, object]:
    return {
        "relation_revision_id": ids.new("object-relation-revision"),
        "relation_id": ids.new("object-relation"),
        "project_id": current.project_id,
        "source_object_id": current.object_id,
        "relation_type": relation_type,
        "target_ref": target_ref,
        "semantic_role": semantic_role,
        "evidence_refs": evidence_refs,
        "actor_or_agent_ref": actor_ref,
        "valid_from": now,
        "authority_state": authority_state,
        "active": True,
        "created_at": now,
    }


def ended_object_relation_draft(
    relation: ObjectRelationRecord, *, ids: IdGeneratorPort, clock: ClockPort
) -> dict[str, object]:
    draft = relation.model_dump(mode="python")
    draft.update(
        {
            "relation_revision_id": ids.new("object-relation-revision"),
            "valid_to": clock.now(),
            "active": False,
            "supersedes_revision_digest": relation.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return draft


def object_attention_draft(
    current: DecisionObjectRecord,
    *,
    attention_type: str,
    risk: str,
    reason: str,
    condition_ref: str,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    return {
        "attention_revision_id": ids.new("object-attention-revision"),
        "attention_id": ids.new("object-attention"),
        "project_id": current.project_id,
        "object_id": current.object_id,
        "attention_type": attention_type,
        "risk": risk,
        "reason": reason,
        "condition_ref": condition_ref,
        "state": "OPEN",
        "created_at": clock.now(),
    }


def acknowledged_object_attention_draft(
    attention: ObjectAttentionRecord,
    *,
    resolved: bool,
    actor_ref: str,
    note: str | None,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft = attention.model_dump(mode="python")
    draft.update(
        {
            "attention_revision_id": ids.new("object-attention-revision"),
            "state": "CLEARED" if resolved else "ACKNOWLEDGED",
            "acknowledged_by": actor_ref,
            "acknowledgement_note": note,
            "supersedes_revision_digest": attention.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return draft
