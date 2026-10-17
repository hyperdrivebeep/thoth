"""Pure record construction for the MEMORY control routes (96b).

These functions only build payloads and digests from values the handler has already read and
validated. They read nothing, write nothing and never see a store, a ledger, a clock or an id
generator; the handler keeps every guard, read, create call and its order.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import cast

from thoth.application.commands.memory_control_profiles import INJECTION, POLICIES, SECRET
from thoth.application.commands.memory_inputs import (
    CandidateClassifyInput,
    CandidateCreateInput,
    ContextBuildInput,
    ProjectionRebuildInput,
    ValidateInput,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord


def candidate_payload(
    request: CandidateCreateInput,
) -> tuple[dict[str, object], bool, list[str]]:
    raw = request.assertion_candidate or ""
    unsafe = bool(INJECTION.search(raw) or SECRET.search(raw))
    missing: list[str] = []
    if request.payload_mode == "DOMAIN_REFERENCE" and not request.domain_revision_ref:
        missing.append("domain_revision_ref")
    if request.payload_mode == "MEMORY_ASSERTION" and not raw:
        missing.append("assertion_candidate")
    risk_tags = (*request.risk_tags, "UNSAFE_CONTENT") if unsafe else request.risk_tags
    payload: dict[str, object] = {
        "producer_role": request.producer_role,
        "payload_mode": request.payload_mode,
        "recall_class": request.recall_class,
        "domain_revision_ref": request.domain_revision_ref,
        "assertion_candidate": "[REDACTED_TOMBSTONE]" if unsafe else request.assertion_candidate,
        "raw_payload_retained": not unsafe,
        "parent_refs": request.parent_refs,
        "scope": request.scope,
        "evidence_refs": request.evidence_refs,
        "risk_tags": tuple(dict.fromkeys(risk_tags)),
        "classification_status": "AMBIGUOUS" if missing else "CLEAR",
        "deterministic_precheck": "QUARANTINE" if unsafe else "PASS" if not missing else "HOLD",
        "missing_gates": tuple(missing),
        "allowed_use": "AUDIT_ONLY" if unsafe else "EVIDENCE_SEARCH_ONLY",
    }
    return payload, unsafe, missing


def classified_payload(
    current: ControlRecord, request: CandidateClassifyInput
) -> tuple[str, tuple[str, ...], str, dict[str, object]]:
    recall_class = str(current.payload.get("recall_class"))
    alternatives = ()
    status = "CLEAR"
    if recall_class not in {
        str(item) for policy in POLICIES for item in cast(tuple[str, ...], policy["recall_classes"])
    }:
        status = "AMBIGUOUS"
        alternatives = ("REFERENCE", "LESSON")
    payload = {
        **current.payload,
        "classification_status": status,
        "alternative_classes": alternatives,
        "classifier_profile_ref": request.classifier_profile_ref,
        "required_role_union": gate_plan(current.payload)["required_roles"],
    }
    return recall_class, alternatives, status, payload


def gate_draft(
    request: ValidateInput, current: ControlRecord
) -> tuple[dict[str, object], str, dict[str, str], dict[str, object], str]:
    plan = gate_plan(current.payload)
    unsafe = current.state == "QUARANTINED"
    ambiguous = current.payload.get("classification_status") == "AMBIGUOUS"
    reducer = "QUARANTINED" if unsafe else "HELD" if ambiguous else "VALIDATED"
    verdicts = {
        role: "REJECT" if unsafe else "HOLD" if ambiguous else "PASS"
        for role in cast(tuple[str, ...], plan["required_roles"])
    }
    gate_payload: dict[str, object] = {
        "subject_id": current.record_id,
        "transition_intent": request.transition_intent,
        "policy_version": request.policy_version,
        "required_roles": plan["required_roles"],
        "actual_roles": tuple(verdicts),
        "verdicts": verdicts,
        "deterministic_precheck": current.payload.get("deterministic_precheck"),
        "reducer_outcome": reducer,
    }
    gate_digest = domain_digest("MEMORY_GATE", "1.0.0", canonical_payload(gate_payload))
    return plan, reducer, verdicts, gate_payload, gate_digest


def memory_entry_payload(candidate: ControlRecord) -> dict[str, object]:
    entry_payload: dict[str, object] = {
        "candidate_id": candidate.record_id,
        "payload_mode": candidate.payload.get("payload_mode"),
        "recall_class": candidate.payload.get("recall_class"),
        "domain_revision_ref": candidate.payload.get("domain_revision_ref"),
        "assertion": candidate.payload.get("assertion_candidate"),
        "parent_refs": candidate.payload.get("parent_refs", ()),
        "scope": candidate.payload.get("scope", {}),
        "evidence_refs": candidate.payload.get("evidence_refs", ()),
        "pipeline_status": "CANDIDATE",
        "support_status": "UNRESOLVED",
        "authority_status": "UNKNOWN",
        "lifecycle_status": "ACTIVE",
        "recall_eligibility": "EVIDENCE_SEARCH_ONLY",
        "classification_status": candidate.payload.get("classification_status"),
    }
    return entry_payload


def context_pack_payload(
    request: ContextBuildInput,
    entries: tuple[ControlRecord, ...],
    heads: AbstractSet[str],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    included: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    used = 0
    for item in entries:
        payload = item.payload
        reason = None
        owner = str(payload.get("domain_revision_ref") or "")
        if owner and owner not in heads:
            reason = "OWNER_REVISION_NOT_CURRENT"
        elif payload.get("lifecycle_status") != "ACTIVE":
            reason = "NOT_ACTIVE"
        elif payload.get("recall_eligibility") not in {"WORKING_CONTEXT", "ACTION_CONTEXT"}:
            reason = "NOT_ELIGIBLE_FOR_TARGET_USE"
        elif payload.get("scope") and not scope_matches(payload.get("scope"), request.scope):
            reason = "SCOPE_MISMATCH"
        size = len(str(payload.get("assertion") or payload.get("domain_revision_ref") or ""))
        if reason is None and used + size > request.token_budget * 4:
            reason = "TOKEN_BUDGET"
        if reason is None:
            included.append(
                {
                    "memory_entry_id": item.record_id,
                    "revision_digest": item.record_digest,
                    "reason": "DETERMINISTIC_FILTER_AND_RELEVANCE",
                }
            )
            used += size
        else:
            excluded.append({"memory_entry_id": item.record_id, "reason": reason})
    pack_payload: dict[str, object] = {
        "thread_id": request.thread_id,
        "query": request.query,
        "target_use": request.target_use,
        "scope": request.scope,
        "cutoff": request.cutoff,
        "selection_policy_version": request.selection_policy_version,
        "included": tuple(included),
        "excluded": tuple(excluded),
        "conflict_refs": tuple(
            item.record_id
            for item in entries
            if item.payload.get("support_status") == "CONFLICTING"
        ),
        "token_budget": request.token_budget,
        "approximate_characters": used,
        "query_policy_digest": domain_digest(
            "MEMORY_QUERY_POLICY",
            "1.0.0",
            canonical_payload(
                {
                    "query": request.query,
                    "scope": request.scope,
                    "policy": request.selection_policy_version,
                }
            ),
        ),
    }
    return included, pack_payload


def projection_checkpoint(request: ProjectionRebuildInput, source_set: tuple[str, ...]) -> str:
    checkpoint = domain_digest(
        "MEMORY_PROJECTION_CHECKPOINT",
        "1.0.0",
        canonical_payload(
            {
                "sources": source_set,
                "types": request.projection_types,
                "version": request.model_or_index_version,
            }
        ),
    )
    return checkpoint


def retention_candidates(
    entries: tuple[ControlRecord, ...],
) -> tuple[dict[str, str | bool], ...]:
    candidates = tuple(
        {
            "memory_entry_id": item.record_id,
            "transition": "RETIRE_CANDIDATE"
            if item.payload.get("lifecycle_status") == "EXPIRED"
            else "RETAIN",
            "silent_delete": False,
        }
        for item in entries
    )
    return candidates


def gate_plan(payload: dict[str, object]) -> dict[str, object]:
    mode = payload.get("payload_mode")
    roles = (
        ("FACTS", "CONTRARIAN", "RISK", "GATE_REVIEWER")
        if mode == "DOMAIN_REFERENCE"
        else ("REFLECTION", "CONTRARIAN", "RISK", "GATE_REVIEWER")
    )
    return {"required_roles": roles, "majority_vote": False}


def scope_matches(left: object, right: dict[str, str]) -> bool:
    if not isinstance(left, dict):
        return False
    return all(
        right.get(str(key)) == str(value)
        for key, value in cast(dict[object, object], left).items()
        if key != "workstream"
    )
