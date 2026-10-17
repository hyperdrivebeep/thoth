"""Model-driven hypothesis review and bounded action-plan repair for one cycle."""

from thoth.application.services.action_compiler import compile_action_plan
from thoth.application.services.behavior_context import should_semantic_repair
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.research_identity_service import HypothesisOwnershipBatch
from thoth.application.workflows.thread_cycle_contracts import ThreadCycleCommand
from thoth.domain.action import ActionPlanCompilationResult, ActionPlanDraft
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import HypothesisSemanticReview
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_review_validation import review_coverage, target_ids
from thoth.domain.memory_preparation import MemoryPreparationBasis
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.research_execution import ResearchWork
from thoth.ports.model import ModelOutputContractHold, ModelPort


async def review_hypotheses(
    command: ThreadCycleCommand,
    context: ContextPack,
    portfolio: HypothesisPortfolio,
    hypothesis_batch: HypothesisOwnershipBatch,
    work: ResearchWork,
    *,
    model: ModelPort,
) -> tuple[HypothesisSemanticReview, ContextPack]:
    assert hypothesis_batch is not None
    if work.show_draft is not None:
        work.show_draft(
            "HYPOTHESIS_REVIEW",
            {
                "state": "DRAFT_NOT_COMMITTED",
                "portfolio": portfolio.model_dump(mode="json"),
                "proposed_revision_refs": [h.revision_digest for h in hypothesis_batch.hypotheses],
            },
        )
    review_result = await model.structured(
        ModelRequest(
            role=ModelRole.HYPOTHESIS_REVIEWER,
            project_id=command.project_id,
            cutoff_at=command.cutoff_at,
            context_pack=context.model_copy(
                update={
                    "candidate_portfolio": portfolio,
                    "research_context": {
                        **work.context,
                        "hypothesis_review_target_ids": list(target_ids(portfolio)),
                        "task": "Review the exact proposed full revisions. "
                        "Return exactly one decision for every input hypothesis_id; "
                        "if evidence is insufficient for an ID, mark that ID INCONCLUSIVE. "
                        "Evaluate entailment, counterevidence, uncertainty "
                        "and discriminating tests. Zero or one hypothesis is allowed; "
                        "preserve alternatives considered and next checks. "
                        "Do not invent 11-axis scores, independent evaluation "
                        "or human calibration. "
                        "Keep promotion HOLD where profile requirements are unverified.",
                        "proposed_full_hypotheses": hypothesis_batch.hypotheses,
                        "proposed_full_portfolio": hypothesis_batch.portfolio,
                    },
                }
            ),
            output_model=HypothesisSemanticReview,
            prompt_version="hypothesis_semantic_review.v5",
            model_policy_ref=command.model_policy_ref,
            max_output_tokens=5000,
        )
    )
    from thoth.application.services.research_decision_gates import qualify_hypothesis_review

    hypothesis_review = qualify_hypothesis_review(review_result.output, work)
    coverage = review_coverage(portfolio, hypothesis_review.decisions)
    if not coverage.ok:
        raise ModelOutputContractHold("HYPOTHESIS_REVIEW_COVERAGE_MISMATCH")
    if any(
        set(d.evidence_refs) - {s.span_id for s in command.evidence}
        for d in hypothesis_review.decisions
    ):
        raise ValueError("HYPOTHESIS_REVIEW_UNKNOWN_EVIDENCE")
    work.context["hypothesis_review"] = hypothesis_review.model_dump(mode="json")
    work.context["hypothesis_revision_refs"] = [
        h.revision_digest for h in hypothesis_batch.hypotheses
    ]
    context = context.model_copy(update={"research_context": work.context})
    return hypothesis_review, context


async def plan_actions(
    command: ThreadCycleCommand,
    context: ContextPack,
    portfolio: HypothesisPortfolio,
    memory_basis: MemoryPreparationBasis | None,
    *,
    model: ModelPort,
    full_memory: FullProjectMemoryService | None,
    max_output_tokens: int,
) -> tuple[ModelResult[ActionPlanDraft], ActionPlanCompilationResult, bool]:
    action_result = await model.structured(
        ModelRequest(
            role=ModelRole.ACTION_PLANNER,
            project_id=command.project_id,
            cutoff_at=command.cutoff_at,
            context_pack=context.model_copy(update={"candidate_portfolio": portfolio}),
            output_model=ActionPlanDraft,
            prompt_version="action_alternatives.v3",
            model_policy_ref=command.model_policy_ref,
            max_output_tokens=max_output_tokens,
        )
    )
    semantic_repair_attempted = False
    action_compilation = compile_action_plan(
        action_result.output,
        evidence_ids=frozenset(span.span_id for span in command.evidence),
        hypothesis_ids=frozenset(hypothesis.hypothesis_id for hypothesis in portfolio.hypotheses),
        policy=command.action_policy,
    )
    if should_semantic_repair(action_compilation.plan is None):
        semantic_repair_attempted = True
        repair_context = context.model_copy(
            update={
                "policy_hints": {
                    **context.policy_hints,
                    "semantic_repair_attempt": 1,
                    "action_compilation_hold_reasons": action_compilation.hold_reasons,
                    "allowed_evidence_ids": tuple(span.span_id for span in command.evidence),
                    "allowed_hypothesis_ids": tuple(
                        hypothesis.hypothesis_id for hypothesis in portfolio.hypotheses
                    ),
                    "repair_contract": (
                        "Replace the rejected draft. Preserve no rejected action IDs unless "
                        "they can be made valid. Use only allowed IDs, at least two materially "
                        "different action families, and at least one safe R0-R2 frontier item."
                    ),
                }
            }
        )
        if full_memory is not None and memory_basis is not None:
            full_memory.require_authority(memory_basis)
        repair_result = await model.structured(
            ModelRequest(
                role=ModelRole.ACTION_PLANNER,
                project_id=command.project_id,
                cutoff_at=command.cutoff_at,
                context_pack=repair_context.model_copy(update={"candidate_portfolio": portfolio}),
                output_model=ActionPlanDraft,
                prompt_version="action_alternatives.semantic_repair.v3",
                model_policy_ref=command.model_policy_ref,
                max_output_tokens=max_output_tokens,
            )
        )
        action_compilation = compile_action_plan(
            repair_result.output,
            evidence_ids=frozenset(span.span_id for span in command.evidence),
            hypothesis_ids=frozenset(
                hypothesis.hypothesis_id for hypothesis in portfolio.hypotheses
            ),
            policy=command.action_policy,
        )
        if action_compilation.plan is None:
            raise ValueError(
                "action plan compilation held after one semantic repair: "
                + "; ".join(action_compilation.hold_reasons)
            )
        action_result = repair_result
    return action_result, action_compilation, semantic_repair_attempted
