"""Pure construction of the Outcome records, judgements and built-in profile catalog.

Each function builds a value from its arguments. The id and clock arguments are the service's own
port objects and are called in the order the service called them. Nothing here reads or writes
storage, opens a transaction, checks a guard or audits; those stay in OutcomeService.
"""

from __future__ import annotations

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.outcome_full import (
    AttributionAssessmentRecord,
    OutcomeAssessmentRecord,
    OutcomeChangeSetRecord,
    OutcomeImpactRecord,
    OutcomeProfileRecord,
    OutcomeSeriesRecord,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort

BUILTIN_OUTCOME_PROFILES = (
    (
        "REQUIREMENT_VERIFICATION_OUTCOME",
        "Requirement verification outcome",
        ("REQUIREMENT_VERIFICATION", "EXPERIMENT_TEST"),
        ("observation", "comparator", "criterion"),
        ("requirement_result", "effect_direction", "residual_risk"),
        "CONTRIBUTION_ONLY_WITHOUT_CAUSAL_DESIGN",
    ),
    (
        "EXPERIMENT_LEARNING_OUTCOME",
        "Experiment learning outcome",
        ("HYPOTHESIS_DISCRIMINATION", "ANALYSIS_COMPUTATION", "SIMULATION"),
        ("observation", "comparator", "test_validity"),
        ("hypothesis_discrimination", "information_gain", "unexpected_effect"),
        "ASSOCIATION_BY_DEFAULT",
    ),
    (
        "DELIVERY_ACKNOWLEDGEMENT_OUTCOME",
        "Delivery acknowledgement outcome",
        ("COMMUNICATION_SUBMISSION",),
        ("delivery_receipt",),
        ("delivery_acknowledgement",),
        "NOT_APPLICABLE",
    ),
)


def record_digest(kind: str, value: dict[str, object]) -> str:
    return domain_digest(kind, "1.0.0", canonical_payload(value))


def outcome_profile_record(
    profile_ref: str,
    name: str,
    purposes: tuple[str, ...],
    required: tuple[str, ...],
    dimensions: tuple[str, ...],
    attribution: str,
) -> OutcomeProfileRecord:
    draft: dict[str, object] = {
        "profile_ref": profile_ref,
        "version": 1,
        "name": name,
        "action_purposes": purposes,
        "required_evidence": required,
        "comparator_contract": "EXACT_BASELINE_SET_DIGEST_REQUIRED",
        "window_contract": "PHASE_AND_TIME_WINDOW_REQUIRED",
        "evaluator_contract": "SOURCE_BOUND_DETERMINISTIC_OR_EXPLICIT_ABSTENTION",
        "effect_dimensions": dimensions,
        "attribution_standard": attribution,
        "closure_criteria": {
            "validity": "VALID_OR_PROFILE_ACCEPTED_LIMITED",
            "objective": "EXPLICITLY_ASSESSED",
            "residual_effect": "HANDLED_OR_ACCEPTED",
        },
        "enabled": True,
        "authority_owner": "OUTCOME_EVALUATION_OWNER",
    }
    return OutcomeProfileRecord.model_validate(
        {
            **draft,
            "profile_digest": record_digest("OUTCOME_PROFILE", draft),
        }
    )


def missing_series_fields(
    assessment_windows: tuple[dict[str, object], ...], comparison_baseline_set_digest: str
) -> tuple[str, ...]:
    return tuple(
        field
        for field, present in (
            ("assessment_windows", bool(assessment_windows)),
            ("comparison_baseline_set_digest", bool(comparison_baseline_set_digest)),
            ("profile", True),
        )
        if not present
    )


def series_record(
    project_id: str,
    object_id: str,
    action_plan_revision_digest: str,
    planned_execution_ref: str | None,
    profile_ref: str,
    comparison_baseline_set_digest: str,
    assessment_windows: tuple[dict[str, object], ...],
    phases: tuple[str, ...],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeSeriesRecord:
    draft: dict[str, object] = {
        "series_revision_id": ids.new("outcome-series-revision"),
        "outcome_series_id": ids.new("outcome-series"),
        "project_id": project_id,
        "object_id": object_id,
        "action_plan_revision_digest": action_plan_revision_digest,
        "planned_execution_ref": planned_execution_ref,
        "profile_ref": profile_ref,
        "comparison_baseline_set_digest": comparison_baseline_set_digest,
        "assessment_windows": assessment_windows,
        "phase_states": {phase: "WAITING_OBSERVATION" for phase in phases},
        "observation_refs_by_phase": {},
        "observation_completeness_by_phase": {},
        "assessment_refs": (),
        "final_within_scope_state": "NOT_ASSESSED",
        "revision": 0,
        "created_at": clock.now(),
    }
    return OutcomeSeriesRecord.model_validate(
        {**draft, "revision_digest": record_digest("OUTCOME_SERIES", draft)}
    )


def linked_phase_updates(
    current: OutcomeSeriesRecord,
    phase: str,
    observation_refs: tuple[str, ...],
    completeness: str,
) -> dict[str, object]:
    refs = dict(current.observation_refs_by_phase)
    refs[phase] = observation_refs
    completeness_by_phase = dict(current.observation_completeness_by_phase)
    completeness_by_phase[phase] = completeness
    states = dict(current.phase_states)
    states[phase] = (
        "READY_TO_ASSESS"
        if observation_refs and completeness in {"COMPLETE", "PARTIAL"}
        else "WAITING_OBSERVATION"
    )
    return {
        "observation_refs_by_phase": refs,
        "observation_completeness_by_phase": completeness_by_phase,
        "phase_states": states,
    }


def judge_assessment(
    current: OutcomeSeriesRecord,
    phase: str,
    observation_refs: tuple[str, ...],
    comparator_refs: tuple[str, ...],
) -> tuple[str, list[str], str]:
    completeness = current.observation_completeness_by_phase.get(phase, "MISSING")
    validity = (
        "INVALID"
        if completeness == "CORRUPT"
        else "VALID"
        if completeness == "COMPLETE" and observation_refs and comparator_refs
        else "LIMITED"
        if completeness == "PARTIAL" and observation_refs
        else "INCONCLUSIVE"
    )
    limitations: list[str] = []
    if not comparator_refs:
        limitations.append("comparator evidence is missing")
    if completeness != "COMPLETE":
        limitations.append(f"observation completeness is {completeness}")
    attribution = (
        "ASSOCIATED" if current.planned_execution_ref and observation_refs else "NOT_ASSESSED"
    )
    return validity, limitations, attribution


def assessment_record(
    current: OutcomeSeriesRecord,
    profile: OutcomeProfileRecord,
    phase: str,
    observation_refs: tuple[str, ...],
    comparator_refs: tuple[str, ...],
    assumptions: tuple[str, ...],
    validity: str,
    limitations: list[str],
    attribution: str,
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeAssessmentRecord:
    draft: dict[str, object] = {
        "assessment_revision_id": ids.new("outcome-assessment-revision"),
        "outcome_assessment_id": ids.new("outcome-assessment"),
        "project_id": current.project_id,
        "outcome_series_id": current.outcome_series_id,
        "object_id": current.object_id,
        "assessment_phase": phase,
        "plan_revision_digest": current.action_plan_revision_digest,
        "execution_ref": current.planned_execution_ref,
        "baseline_set_digest": current.comparison_baseline_set_digest,
        "profile_ref": profile.profile_ref,
        "profile_version": profile.version,
        "expected_observation_refs": (),
        "actual_observation_refs": observation_refs,
        "comparator_refs": comparator_refs,
        "assumptions": assumptions,
        "lifecycle": "ASSESSED",
        "validity": validity,
        "objective_attainment": "NOT_ASSESSED",
        "attribution_state": attribution,
        "follow_up_state": "MORE_EVIDENCE_REQUIRED",
        "limitations": tuple(limitations),
        "changed_dimensions": tuple(profile.effect_dimensions),
        "created_at": clock.now(),
    }
    return OutcomeAssessmentRecord.model_validate(
        {**draft, "revision_digest": record_digest("OUTCOME_ASSESSMENT", draft)}
    )


def assessed_series_updates(
    current: OutcomeSeriesRecord,
    phase: str,
    assessment: OutcomeAssessmentRecord,
    validity: str,
) -> dict[str, object]:
    states = dict(current.phase_states)
    states[phase] = "ASSESSED"
    return {
        "phase_states": states,
        "assessment_refs": (
            *current.assessment_refs,
            assessment.outcome_assessment_id,
        ),
        "final_within_scope_state": (
            validity if phase == "FINAL_WITHIN_SCOPE" else current.final_within_scope_state
        ),
    }


def reassessed_record(
    current: OutcomeAssessmentRecord,
    new_evidence_refs: tuple[str, ...],
    reason: str,
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeAssessmentRecord:
    draft = current.model_dump(mode="python")
    merged = tuple(dict.fromkeys((*current.actual_observation_refs, *new_evidence_refs)))
    changed = ("evidence_coverage",) if merged != current.actual_observation_refs else ()
    draft.update(
        {
            "assessment_revision_id": ids.new("outcome-assessment-revision"),
            "actual_observation_refs": merged,
            "lifecycle": "ASSESSED",
            "validity": (
                "LIMITED" if current.validity == "INCONCLUSIVE" and merged else current.validity
            ),
            "changed_dimensions": changed,
            "limitations": (*current.limitations, f"reassessment reason: {reason}"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return OutcomeAssessmentRecord.model_validate(
        {**draft, "revision_digest": record_digest("OUTCOME_ASSESSMENT", draft)}
    )


def attribution_support(
    method: str,
    contextual_factor_refs: tuple[str, ...],
    counterfactual_evidence_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
) -> tuple[str, list[str]]:
    causal_methods = {"RANDOMIZED", "QUASI_EXPERIMENTAL", "VALID_N_OF_1"}
    contribution_methods = {"CONTRIBUTION_ANALYSIS", "PROCESS_TRACING"}
    supported = (
        "CAUSAL_ATTRIBUTION_SUPPORTED"
        if method in causal_methods and counterfactual_evidence_refs and evidence_refs
        else "CONTRIBUTION_SUPPORTED"
        if method in contribution_methods and evidence_refs
        else "ASSOCIATED"
        if evidence_refs
        else "INCONCLUSIVE"
    )
    limitations: list[str] = []
    if supported != "CAUSAL_ATTRIBUTION_SUPPORTED":
        limitations.append("causal attribution design/evidence is insufficient")
    if not contextual_factor_refs:
        limitations.append("contextual factors are not explicitly represented")
    return supported, limitations


def attribution_record(
    current: OutcomeAssessmentRecord,
    method: str,
    contextual_factor_refs: tuple[str, ...],
    counterfactual_evidence_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    supported: str,
    limitations: list[str],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> AttributionAssessmentRecord:
    draft: dict[str, object] = {
        "attribution_assessment_id": ids.new("outcome-attribution"),
        "project_id": current.project_id,
        "outcome_assessment_id": current.outcome_assessment_id,
        "method": method,
        "contextual_factor_refs": contextual_factor_refs,
        "counterfactual_evidence_refs": counterfactual_evidence_refs,
        "evidence_refs": evidence_refs,
        "supported_level": supported,
        "limitations": tuple(limitations),
        "created_at": clock.now(),
    }
    return AttributionAssessmentRecord.model_validate(
        {**draft, "attribution_digest": record_digest("OUTCOME_ATTRIBUTION", draft)}
    )


def change_set_record(
    current: OutcomeAssessmentRecord,
    proposed_entity_changes: dict[str, object],
    impact_policy_ref: str,
    expected_project_head_set: str,
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeChangeSetRecord:
    restrictions: list[str] = []
    if current.validity in {"INVALID", "LIMITED", "INCONCLUSIVE"}:
        restrictions.extend(
            (
                "NO_OFFICIAL_CRITERION_CHANGE",
                "NO_HYPOTHESIS_APPRAISAL_MUTATION",
                "NO_BASELINE_MOVEMENT",
                "NO_INSTITUTIONAL_DISPOSITION",
            )
        )
    candidate_revisions = tuple(
        {
            "entity_namespace": namespace,
            "candidate_patch": patch,
            "applied": False,
        }
        for namespace, patch in proposed_entity_changes.items()
    )
    draft: dict[str, object] = {
        "outcome_change_set_id": ids.new("outcome-change-set"),
        "project_id": current.project_id,
        "outcome_assessment_id": current.outcome_assessment_id,
        "proposed_entity_changes": proposed_entity_changes,
        "impact_policy_ref": impact_policy_ref,
        "expected_project_head_set": expected_project_head_set,
        "candidate_revisions": candidate_revisions,
        "impact_propagation_plan": {
            "stale_refs": tuple(proposed_entity_changes),
            "invalidated_refs": (),
            "recalculate_refs": tuple(proposed_entity_changes),
        },
        "validity_restrictions": tuple(restrictions),
        "status": "PROPOSED_NOT_APPLIED",
        "created_at": clock.now(),
    }
    return OutcomeChangeSetRecord.model_validate(
        {**draft, "change_set_digest": record_digest("OUTCOME_CHANGE_SET", draft)}
    )


def impact_record(
    series: OutcomeSeriesRecord,
    broader_window: str,
    impact_profile_ref: str,
    evidence_refs: tuple[str, ...],
    attribution_design_ref: str,
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeImpactRecord:
    sufficient = bool(
        evidence_refs
        and broader_window
        and attribution_design_ref
        and attribution_design_ref != "TEMPORAL_ONLY"
    )
    draft: dict[str, object] = {
        "impact_assessment_id": ids.new("outcome-impact"),
        "project_id": series.project_id,
        "outcome_series_id": series.outcome_series_id,
        "broader_window": broader_window,
        "impact_profile_ref": impact_profile_ref,
        "evidence_refs": evidence_refs,
        "attribution_design_ref": attribution_design_ref,
        "status": ("CANDIDATE" if sufficient else "INSUFFICIENT_CAUSAL_DESIGN"),
        "contextual_factors": (),
        "limitations": (
            () if sufficient else ("broader impact cannot be inferred from one execution alone",)
        ),
        "created_at": clock.now(),
    }
    return OutcomeImpactRecord.model_validate(
        {**draft, "impact_digest": record_digest("OUTCOME_IMPACT", draft)}
    )
