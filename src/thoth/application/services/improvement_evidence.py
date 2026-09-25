"""Resolve public assessments against existing owner-produced evaluation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.improvement import (
    ImprovementEvaluation,
    ImprovementExposure,
    RecursiveImprovementResult,
)
from thoth.ports.control_record import ControlRecordStorePort


@dataclass(frozen=True)
class BoundEvaluation:
    evaluation: ImprovementEvaluation
    canary_completed: bool
    runtime_failure_reason: str | None = None


def evaluation_failure_reason(evaluation: ImprovementEvaluation) -> str | None:
    if evaluation.hidden_holdout_exposed:
        return "HOLDOUT_LEAK"
    if evaluation.budget_exhausted:
        return "BUDGET_EXHAUSTED"
    if evaluation.timed_out:
        return "TIMEOUT"
    if (
        evaluation.critical_regression
        or evaluation.candidate_safety_bps < evaluation.baseline_safety_bps
    ):
        return "CRITICAL_REGRESSION"
    if evaluation.candidate_quality_bps <= evaluation.baseline_quality_bps:
        return "NO_IMPROVEMENT"
    if evaluation.candidate_cost_microunits > evaluation.baseline_cost_microunits:
        return "COST_REGRESSION"
    return None


def plan_matches_improvement(plan: ControlRecord, improvement: ControlRecord) -> bool:
    return (
        improvement.state == "PROPOSED"
        and plan.record_type == "EVALUATION_PLAN"
        and plan.project_id == improvement.project_id
        and plan.payload.get("improvement_revision_id") == improvement.record_id
        and plan.payload.get("improvement_record_digest") == improvement.record_digest
        and plan.payload.get("candidate_digest") == improvement.payload.get("candidate_digest")
        and plan.payload.get("baseline_digest") == improvement.payload.get("baseline_digest")
    )


def resolve_bound_evaluation(
    records: ControlRecordStorePort,
    *,
    plan: ControlRecord,
    improvement: ControlRecord | None,
    result_refs: tuple[str, ...],
    evaluator_result_refs: tuple[str, ...],
    exposure_ref: str,
) -> BoundEvaluation | None:
    if (
        improvement is None
        or not plan_matches_improvement(plan, improvement)
        or not result_refs
        or not evaluator_result_refs
    ):
        return None
    for record in records.list(plan.project_id, "IMPROVEMENT_RUNTIME", "RUN"):
        try:
            run = RecursiveImprovementResult.model_validate(record.payload)
        except ValueError:
            continue
        evaluation = run.evaluation
        if (
            record.state not in {"PROMOTED_LOCAL", "ROLLED_BACK"}
            or run.state.value != record.state
            or run.project_id != plan.project_id
            or evaluation is None
            or not _valid_run_digest(run)
            or not set(result_refs).issubset({run.run_id, run.receipt_digest})
            or not set(evaluator_result_refs).issubset({evaluation.evaluation_digest})
            or run.candidate_digest != plan.payload.get("candidate_digest")
            or run.baseline_digest != plan.payload.get("baseline_digest")
            or evaluation.candidate_digest != run.candidate_digest
            or evaluation.baseline_digest != run.baseline_digest
            or evaluation.fixture_digest != run.fixture_digest
            or evaluation.evaluator_id != run.evaluator_id
        ):
            continue
        evaluators = plan.payload.get("evaluator_refs")
        datasets = plan.payload.get("datasets")
        if (
            not isinstance(evaluators, list | tuple)
            or evaluation.evaluator_id not in evaluators
            or not isinstance(datasets, list | tuple)
            or not any(
                isinstance(item, dict)
                and cast(dict[str, object], item).get("fixture_digest") == run.fixture_digest
                for item in cast(list[object] | tuple[object, ...], datasets)
            )
        ):
            continue
        expected_digest = domain_digest(
            "IMPROVEMENT_EVALUATION",
            "1.0.0",
            canonical_payload(evaluation.model_dump(mode="python", exclude={"evaluation_digest"})),
        )
        if expected_digest != evaluation.evaluation_digest:
            continue
        exposure = next((item for item in run.exposures if item.exposure_id == exposure_ref), None)
        stored = records.read(plan.project_id, "IMPROVEMENT_RUNTIME", exposure_ref)
        if exposure is None or stored is None or stored.record_type != "EXPOSURE":
            continue
        try:
            persisted = ImprovementExposure.model_validate(stored.payload)
        except ValueError:
            continue
        if (
            persisted != exposure
            or stored.state != exposure.stage
            or exposure.project_id != plan.project_id
            or exposure.candidate_digest != run.candidate_digest
            or exposure.baseline_digest != run.baseline_digest
            or exposure.fixture_digest != run.fixture_digest
            or exposure.evaluator_id != evaluation.evaluator_id
            or not exposure.exact_candidate_digest
            or exposure.exposure_digest
            != domain_digest(
                "IMPROVEMENT_EXPOSURE",
                "1.0.0",
                canonical_payload(exposure.model_dump(mode="python", exclude={"exposure_digest"})),
            )
        ):
            continue
        return BoundEvaluation(
            evaluation, run.exact_digest_canary and exposure.stage == "CANARY", run.rollback_reason
        )
    return None


def _valid_run_digest(run: RecursiveImprovementResult) -> bool:
    payload = run.model_dump(mode="python", exclude={"receipt_digest"})
    if (
        domain_digest("RECURSIVE_IMPROVEMENT_RECEIPT", "1.0.0", canonical_payload(payload))
        == run.receipt_digest
    ):
        return True
    # Optional fields were added in successive local versions; preserve each old shape.
    for optional in (
        ("paired_evaluation_id", "paired_result_digest"),
        ("evaluation_provenance",),
        ("behavior_artifact_id", "behavior_artifact_kind", "behavior_registry_active_digest"),
    ):
        if any(payload.get(key) is not None for key in optional):
            return False
        for key in optional:
            payload.pop(key, None)
        if (
            domain_digest("RECURSIVE_IMPROVEMENT_RECEIPT", "1.0.0", canonical_payload(payload))
            == run.receipt_digest
        ):
            return True
    return False


def assessments_match(
    assessments: tuple[ControlRecord | None, ...],
    improvement: ControlRecord,
) -> bool:
    return bool(assessments) and all(
        item is not None
        and item.state == "EVALUATED"
        and plan_matches_improvement(item, improvement)
        and item.payload.get("promotion_eligible") is True
        for item in assessments
    )
