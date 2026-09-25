"""Process observations stay neutral until a separate bound test producer evaluates them."""

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from thoth.application.services.sandbox_service import SandboxExecutionBundle
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EvidenceEffect, HypothesisStatus, OutcomeStatus, PortfolioStatus
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.outcome import HypothesisEvidenceUpdate, OutcomeRecord
from thoth.domain.project import Project
from thoth.domain.r2_loop import HypothesisExecutionAppraisal, R2ExecutionSummary, R2OutcomeValidity
from thoth.domain.sandbox import SandboxRunSpec
from thoth.ports.runtime import ClockPort, IdGeneratorPort


@dataclass(frozen=True)
class ProcessOutcomeProjection:
    observation_refs: tuple[str, ...]
    validity: R2OutcomeValidity
    appraisal: HypothesisExecutionAppraisal
    outcome: OutcomeRecord
    portfolio: HypothesisPortfolio
    plan: ActionPlan
    next_order: tuple[str, ...]


def build_process_outcome(
    *,
    project: Project,
    portfolio: HypothesisPortfolio,
    action_plan: ActionPlan,
    action: ActionCandidate,
    spec: SandboxRunSpec,
    bundle: SandboxExecutionBundle,
    sealed_head_digest: str,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> ProcessOutcomeProjection:
    observation_refs = tuple(item.span_id for item in bundle.observation.evidence_candidates)
    validity_draft: dict[str, object] = {
        "process_state": bundle.result.state.value,
        "observation_state": "SEALED" if observation_refs else "MISSING",
        "comparator_state": "MISSING",
        "scientific_truth_state": "NOT_CERTIFIED",
        "criterion_state": "NOT_ASSESSED",
        "reasons": (
            "sandbox process state is recorded independently from scientific validity",
            "no comparator or criterion disposition was evaluated",
        ),
    }
    validity = R2OutcomeValidity.model_validate(
        {
            **validity_draft,
            "validity_digest": domain_digest(
                "R2_OUTCOME_VALIDITY", "1.0.0", canonical_payload(validity_draft)
            ),
        }
    )
    hypothesis_id = action.hypothesis_ids[0]
    appraisal_draft: dict[str, object] = {
        "action_id": action.action_id,
        "attempt_id": spec.attempt_id,
        "observation_refs": observation_refs,
        "effect": EvidenceEffect.NEUTRAL,
        "reason": (
            "process observation is neutral until comparator and scientific validity gates pass"
        ),
    }
    appraisal = HypothesisExecutionAppraisal.model_validate(
        {
            **appraisal_draft,
            "appraisal_digest": domain_digest(
                "HYPOTHESIS_EXECUTION_APPRAISAL",
                "1.0.0",
                canonical_payload(appraisal_draft),
            ),
        }
    )
    current_head_digest = sealed_head_digest
    outcome = OutcomeRecord(
        outcome_id=ids.new("r2-outcome"),
        project_id=project.project_id,
        action_id=action.action_id,
        status=(OutcomeStatus.OBSERVED if observation_refs else OutcomeStatus.INCONCLUSIVE),
        observed_evidence_refs=observation_refs,
        hypothesis_updates=(
            HypothesisEvidenceUpdate(
                hypothesis_id=hypothesis_id,
                evidence_span_id=observation_refs[0],
                effect=EvidenceEffect.NEUTRAL,
                weight=Decimal(0),
                reason=appraisal.reason,
            ),
        )
        if observation_refs
        else (),
        interpretation="sandbox process output was observed; scientific truth is not certified",
        limitations=(
            "no comparator assessment was performed",
            "criterion attainment and causal attribution remain NOT_ASSESSED",
        ),
        recorded_at=clock.now(),
        input_head_set_digest=spec.current_head_set_digest or current_head_digest,
    )
    revised_hypotheses = tuple(
        item.model_copy(
            update={
                "status": HypothesisStatus.EMPIRICALLY_UPDATED,
                "execution_appraisal": appraisal,
            }
        )
        if item.hypothesis_id == hypothesis_id
        else item
        for item in portfolio.hypotheses
    )
    revised_portfolio = portfolio.model_copy(
        update={
            "hypotheses": revised_hypotheses,
            "status": PortfolioStatus.EMPIRICALLY_UPDATED,
            "generated_from_head_set": current_head_digest,
        }
    )
    next_order = tuple(
        item.action_id for item in action_plan.alternatives if item.action_id != action.action_id
    )
    summary = R2ExecutionSummary(
        action_id=action.action_id,
        attempt_id=spec.attempt_id,
        sandbox_receipt_digest=bundle.receipt.receipt_digest,
        observation_refs=observation_refs,
        outcome_id=outcome.outcome_id,
        validity=validity,
        current_head_set_digest=spec.current_head_set_digest or current_head_digest,
        input_context_digest=cast(str, spec.input_context_digest),
    )
    revised_plan = action_plan.model_copy(
        update={
            "frontier": tuple(item for item in action_plan.frontier if item != action.action_id),
            "next_action_order": next_order,
            "last_r2_execution": summary,
        }
    )
    return ProcessOutcomeProjection(
        observation_refs, validity, appraisal, outcome, revised_portfolio, revised_plan, next_order
    )
