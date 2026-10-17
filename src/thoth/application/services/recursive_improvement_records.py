"""Pure observation, request and receipt construction for recursive improvement.

The coordinator retains evaluator calls, awaits, cancellation, replay, both UoW scopes, writers,
IDs and clocks. These builders consume typed values already held by the coordinator.
"""

from __future__ import annotations

from pydantic import AwareDatetime

from thoth.domain.behavior_artifact import BehaviorArtifact, BehaviorRegistryEntry
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import PairedRunRecord
from thoth.domain.improvement import (
    ImprovementEvaluation,
    ImprovementEvaluationRequest,
    ImprovementExposure,
    ImprovementRunState,
    RecursiveImprovementResult,
)
from thoth.domain.improvement_observation import ImprovementObservation


def untriggered_observation(projection: dict[str, object] | None) -> ImprovementObservation:
    return ImprovementObservation(
        trigger_state="NOT_TRIGGERED",
        reason_code="NO_EXECUTION"
        if projection is None
        else "NO_FAILURE"
        if projection.get("terminal_state") == "COMPLETED"
        else "NO_QUALIFYING_FAILURE_EVENT",
    )


def result_observation(
    result: RecursiveImprovementResult, event: str | None
) -> ImprovementObservation:
    state = (
        "OBSERVED"
        if result.state == ImprovementRunState.BELOW_THRESHOLD
        else "EVALUATED"
        if result.paired_evaluation_id is not None or result.evaluation is not None
        else "HELD"
    )
    return ImprovementObservation(
        trigger_state=state,
        reason_code=result.state.value,
        failure_observation_ref=None if event is None else f"failure:{event}",
        evaluation_run_ref=result.paired_evaluation_id,
        active_baseline_digest=result.active_digest,
        result_ref=result.receipt_digest,
    )


def paired_result(
    result: RecursiveImprovementResult, run: PairedRunRecord | None
) -> RecursiveImprovementResult:
    changes = {
        "paired_evaluation_id": None if run is None else run.spec.pair_id,
        "paired_result_digest": None
        if run is None or run.result is None
        else run.result.result_digest,
        "evaluation_provenance": "MEASURED_PAIR"
        if run is not None and run.result is not None
        else None,
    }
    updated = result.model_copy(update=changes)
    updated = updated.model_copy(
        update={
            "receipt_digest": domain_digest(
                "RECURSIVE_IMPROVEMENT_RECEIPT",
                "1.0.0",
                canonical_payload(updated.model_dump(mode="python", exclude={"receipt_digest"})),
            )
        }
    )
    return updated


def failure_fingerprint(failure_class: str, detail: str, r2_projection: dict[str, object]) -> str:
    return domain_digest(
        "IMPROVEMENT_FAILURE_FINGERPRINT",
        "1.0.0",
        canonical_payload(
            {
                "failure_class": failure_class,
                "failure_detail": detail.casefold().strip(),
                "action_id": str(r2_projection.get("action_id") or "UNKNOWN"),
            }
        ),
    )


def failure_observation_payload(
    thread_id: str, fingerprint: str, failure_class: str, detail: str, baseline: str, count: int
) -> dict[str, object]:
    return {
        "thread_id": thread_id,
        "failure_fingerprint": fingerprint,
        "failure_class": failure_class,
        "failure_detail": detail,
        "baseline_digest": baseline,
        "ordinal": count,
    }


def legacy_candidate(fingerprint: str, failure_class: str) -> tuple[dict[str, object], str]:
    candidate_draft: dict[str, object] = {
        "target_component": "WORKFLOW_DEFINITION",
        "failure_fingerprint": fingerprint,
        "repair_basis": f"typed repeated {failure_class} failure",
        "bounded_change": "add deterministic precondition and repair branch",
    }
    candidate_digest = domain_digest(
        "IMPROVEMENT_CANDIDATE", "1.0.0", canonical_payload(candidate_draft)
    )
    return (candidate_draft, candidate_digest)


def fixture_digests(fingerprint: str) -> tuple[str, str]:
    fixture_digest = domain_digest(
        "FROZEN_IMPROVEMENT_FIXTURE",
        "1.0.0",
        canonical_payload({"failure_fingerprint": fingerprint, "fixture_version": "a08-frozen-v1"}),
    )
    hidden_holdout_digest = domain_digest(
        "HIDDEN_HOLDOUT_OPAQUE_REF", "1.0.0", canonical_payload({"fixture_digest": fixture_digest})
    )
    return (fixture_digest, hidden_holdout_digest)


def seal_evaluation_request(request_draft: dict[str, object]) -> ImprovementEvaluationRequest:
    return ImprovementEvaluationRequest.model_validate(
        {
            **request_draft,
            "request_digest": domain_digest(
                "IMPROVEMENT_EVALUATION_REQUEST", "1.0.0", canonical_payload(request_draft)
            ),
        }
    )


def behavior_result(
    result: RecursiveImprovementResult,
    behavior_candidate: BehaviorArtifact,
    registry: BehaviorRegistryEntry,
) -> RecursiveImprovementResult:
    result = result.model_copy(
        update={
            "behavior_artifact_id": behavior_candidate.artifact_id,
            "behavior_artifact_kind": behavior_candidate.kind.value,
            "behavior_registry_active_digest": registry.active_digest,
        }
    )
    result = result.model_copy(
        update={
            "receipt_digest": domain_digest(
                "RECURSIVE_IMPROVEMENT_RECEIPT",
                "1.0.0",
                canonical_payload(result.model_dump(mode="python", exclude={"receipt_digest"})),
            )
        }
    )
    return result


def result_fields(
    project_id: str,
    thread_id: str,
    state: ImprovementRunState,
    fingerprint: str,
    count: int,
    baseline: str,
    candidate: str | None,
    fixture: str | None,
    evaluator_id: str | None,
    evaluation: ImprovementEvaluation | None,
    exposures: tuple[ImprovementExposure, ...],
    rollback_reason: str | None,
    active: str,
    exact_digest_canary: bool,
    now: AwareDatetime,
) -> dict[str, object]:
    return {
        "project_id": project_id,
        "thread_id": thread_id,
        "state": state.value,
        "failure_fingerprint": fingerprint,
        "failure_count": count,
        "baseline_digest": baseline,
        "candidate_digest": candidate,
        "fixture_digest": fixture,
        "evaluator_id": evaluator_id,
        "evaluation": evaluation,
        "evaluation_provenance": "TEST_ONLY" if evaluation is not None else None,
        "paired_evaluation_id": None,
        "paired_result_digest": None,
        "exposures": exposures,
        "rollback_reason": rollback_reason,
        "active_digest": active,
        "hidden_holdout_exposed": False
        if evaluation is None
        else bool(getattr(evaluation, "hidden_holdout_exposed", False)),
        "exact_digest_canary": exact_digest_canary,
        "official_kpi_changed": False,
        "safety_threshold_changed": False,
        "waiver_changed": False,
        "r4_decision_changed": False,
        "model_weights_changed": False,
        "semantic_truth_certified": False,
        "created_at": now,
    }


def seal_result(draft: dict[str, object]) -> RecursiveImprovementResult:
    return RecursiveImprovementResult.model_validate(
        {
            **draft,
            "receipt_digest": domain_digest(
                "RECURSIVE_IMPROVEMENT_RECEIPT", "1.0.0", canonical_payload(draft)
            ),
        }
    )


def seal_exposure(draft: dict[str, object]) -> ImprovementExposure:
    return ImprovementExposure.model_validate(
        {
            **draft,
            "exposure_digest": domain_digest(
                "IMPROVEMENT_EXPOSURE", "1.0.0", canonical_payload(draft)
            ),
        }
    )
