"""Build Hypothesis drafts while preserving dependency evaluation order; no persistence."""

from __future__ import annotations

from collections.abc import Container, Iterable

from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def creation_draft(
    *,
    project_id: str,
    object_id: str,
    portfolio_id: str,
    statement: str,
    primary_intent: str,
    secondary_intents: tuple[str, ...],
    evidence_basis: str,
    scope: dict[str, str],
    evidence_refs: tuple[str, ...],
    prespecification_state: str,
    object_record: DecisionObjectRecord,
    quality_axes: Iterable[str],
    causal_intents: Container[str],
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    grounded = bool(evidence_refs and evidence_basis.strip() and scope)
    gateway = {
        "atomic_statement": "PASS" if statement.strip() else "FAIL",
        "observed_problem_scope": "PASS" if scope else "FAIL",
        "intent_profile": "PASS",
        "source_cutoff_provenance": "PASS" if evidence_refs else "INCOMPLETE",
        "causal_blocker_separation": "PASS",
        "evidence_basis_label": "PASS" if evidence_basis else "FAIL",
        "expected_outcome_model": "PENDING",
        "observable_test_route": "PENDING",
        "future_oracle_leakage": "PASS",
    }
    quality = {
        axis: (
            "MIXED"
            if axis in {"clarity_atomicity", "problem_scope_alignment", "evidence_basis"}
            and grounded
            else "NOT_ASSESSED"
        )
        for axis in quality_axes
    }
    draft: dict[str, object] = {
        "hypothesis_revision_id": ids.new("hypothesis-revision"),
        "hypothesis_id": ids.new("hypothesis"),
        "project_id": project_id,
        "object_id": object_id,
        "portfolio_id": portfolio_id,
        "statement": statement,
        "observed_problem": object_record.problem_frame,
        "primary_intent": primary_intent,
        "secondary_intents": secondary_intents,
        "intent_profile_refs": (f"intent-profile:{primary_intent.lower()}:1",),
        "evidence_basis": evidence_basis,
        "scope": scope,
        "evidence_refs": evidence_refs,
        "causal_profile": (
            {"applicability": "REQUIRED", "primary_locus": "UNCLASSIFIED"}
            if primary_intent in causal_intents
            else {"applicability": "NOT_APPLICABLE"}
        ),
        "development_stage": "GROUNDED_CANDIDATE" if grounded else "DRAFT",
        "empirical_appraisal": "UNASSESSED",
        "freshness": "CURRENT",
        "prespecification_state": prespecification_state,
        "quality_profile": quality,
        "gateway_results": gateway,
        "created_at": clock.now(),
    }
    return draft


def revision_draft(
    current: HypothesisRecord,
    *,
    updates: dict[str, object],
    invalidate_derived: bool,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft = current.model_dump(mode="python")
    invalidated = current.invalidated_refs
    if invalidate_derived:
        invalidated = tuple(
            dict.fromkeys(
                (*invalidated, *current.prediction_refs, *current.test_refs, "APPRAISALS")
            )
        )
        updates = {
            **updates,
            "prediction_refs": (),
            "test_refs": (),
            "invalidated_refs": invalidated,
            "empirical_appraisal": "UNASSESSED",
            "freshness": "CURRENT",
            "development_stage": (
                "GROUNDED_CANDIDATE"
                if current.evidence_refs
                and updates.get("primary_intent", current.primary_intent) is not None
                else "DRAFT"
            ),
        }
    draft.update(updates)
    draft.update(
        {
            "hypothesis_revision_id": ids.new("hypothesis-revision"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    draft.pop("receipt_ref", None)
    return draft
