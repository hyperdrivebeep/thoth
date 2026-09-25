from __future__ import annotations

from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from thoth.application.reducers import (
    SufficiencySignals,
    assess_information_sufficiency,
    validate_hypothesis_portfolio,
)
from thoth.application.services.action_compiler import compile_action_plan
from thoth.application.services.behavior_context import (
    require_compiled_behavior_plan,
    should_semantic_repair,
)
from thoth.application.services.criterion_projection import criterion_projection
from thoth.application.services.full_project_memory import (
    FullMemoryPromotionResult,
    FullProjectMemoryService,
)
from thoth.application.services.research_identity_service import (
    HypothesisOwnershipBatch,
    ResearchContext,
    ResearchIdentityService,
    ResearchOwnershipBatch,
)
from thoth.application.services.revision_service import (
    CommitDisposition,
    CommitResult,
    RevisionCommitService,
)
from thoth.domain.action import (
    ActionCompilationPolicy,
    ActionPlan,
    ActionPlanCompilationResult,
    ActionPlanDraft,
)
from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import (
    EntityType,
    HypothesisStatus,
    ModelRole,
    OutcomeStatus,
    PortfolioStatus,
    SufficiencyStatus,
)
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.evidence_requirements import (
    HypothesisSemanticReview,
    HypothesisSemanticReviewRecord,
)
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_review_validation import review_coverage, target_ids
from thoth.domain.memory import MemoryRecord
from thoth.domain.memory_preparation import MemoryPreparationBasis, MemoryPreparationHeadChanged
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.relation import DependencyRelation
from thoth.domain.research_execution import ResearchWork, check_research_boundary, research_work
from thoth.domain.research_identity import ResearchIdentityError, decode_research
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import MemoryStorePort
from thoth.ports.model import ModelOutputContractHold, ModelPort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort

ACTION_PLAN_MAX_OUTPUT_TOKENS = 12_000


@dataclass(frozen=True)
class ThreadCycleCommand:
    case_id: str
    project_id: str
    thread_id: str
    object_id: str
    problem: str
    cutoff_at: datetime
    criteria: tuple[CriterionCandidate, ...]
    evidence: tuple[EvidenceSpan, ...]
    sufficiency_signals: SufficiencySignals
    action_policy: ActionCompilationPolicy
    actor: ActorRef
    policy_version: str
    model_policy_ref: str
    expected_head_overrides: dict[str, str] = field(default_factory=dict)
    memory_scope: dict[str, str] = field(default_factory=dict)


class ThreadCycleResult(DomainModel):
    assessment: InformationSufficiencyAssessment
    portfolio: HypothesisPortfolio
    action_plan: ActionPlan
    action_compilation: ActionPlanCompilationResult
    outcome: OutcomeRecord | None = None
    commit: CommitResult
    memory_ids: tuple[str, ...]
    model_ids: tuple[str, ...]
    full_memory: FullMemoryPromotionResult | None = None
    semantic_repair_attempted: bool = False


class ThreadCycleBranchResult(DomainModel):
    project_id: str
    thread_id: str
    terminal_state: Literal["BRANCHED"] = "BRANCHED"
    reason_code: Literal["THREAD_CYCLE_HEAD_CONFLICT"] = "THREAD_CYCLE_HEAD_CONFLICT"
    commit: CommitResult
    downstream_after_branch_executed: Literal[False] = False
    prior_acquisition_completed: bool = False
    input_disposition: Literal["RETAINED_FOR_EXPLICIT_RETRY"] = "RETAINED_FOR_EXPLICIT_RETRY"

    @model_validator(mode="after")
    def require_branch_commit(self) -> ThreadCycleBranchResult:
        if self.commit.disposition != CommitDisposition.BRANCH:
            raise ValueError("branch result requires a branch commit")
        return self


class ThreadCycleService:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        memory: MemoryStorePort,
        model: ModelPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        dependencies: DependencyGraphPort,
        research: ResearchIdentityService,
        unit_of_work: AtomicUnitOfWorkPort | None = None,
        full_memory: FullProjectMemoryService | None = None,
    ) -> None:
        self._ledger = ledger
        self._memory = memory
        self._model = model
        self._commits = commits
        self._clock = clock
        self._ids = ids
        self._dependencies = dependencies
        self._research = research
        self._unit_of_work = unit_of_work
        self._full_memory = full_memory

    async def execute(self, command: ThreadCycleCommand) -> ThreadCycleResult:
        work = research_work.get()
        check_research_boundary()
        heads = dict(self._ledger.read_heads(command.project_id))
        head_digest = domain_digest("WORKING_HEADS", "1.0.0", canonical_payload(heads))
        memory_basis = (
            None
            if self._full_memory is None
            else self._full_memory.capture_basis(
                project_id=command.project_id,
                cutoff_at=command.cutoff_at,
                scope=command.memory_scope,
                actor=command.actor,
            )
        )
        research = self._research.context(command.project_id, command.object_id, heads)
        criterion_staged = self._stage_criteria(command, heads)
        previous_portfolio, previous_action_plan = (
            research.portfolio_view,
            research.action_plan_view,
        )
        assessment = assess_information_sufficiency(
            assessment_id=self._ids.new("assessment"),
            assessment_revision_id=self._ids.new("assessment-revision"),
            project_id=command.project_id,
            target_object_id=command.object_id,
            cutoff_at=command.cutoff_at,
            decision_question=command.problem,
            criteria=command.criteria,
            evidence=command.evidence,
            signals=command.sufficiency_signals,
            policy_version=command.policy_version,
            input_head_set_digest=head_digest,
        )
        context = self._build_model_context(command, assessment, head_digest, research)
        if work is not None:
            context = context.model_copy(update={"research_context": work.context})
        hypothesis_result = await self._model.structured(
            ModelRequest(
                role=ModelRole.HYPOTHESIS_GENERATOR,
                project_id=command.project_id,
                cutoff_at=command.cutoff_at,
                context_pack=context,
                output_model=HypothesisPortfolio,
                prompt_version="hypothesis_portfolio.v2",
                model_policy_ref=command.model_policy_ref,
                max_output_tokens=4_000,
            )
        )
        require_unknown = any(
            status
            in {
                SufficiencyStatus.EVIDENCE_ACQUISITION_REQUIRED,
                SufficiencyStatus.EXPERT_INPUT_REQUIRED,
            }
            for status in assessment.derived_status
        )
        portfolio = validate_hypothesis_portfolio(
            hypothesis_result.output,
            evidence=command.evidence,
            require_unknown_alternative=require_unknown,
        )
        review_id = self._ids.new("hypothesis-semantic-review") if work is not None else None
        if work is not None:
            # A generator cannot grant epistemic or empirical promotion to itself.
            portfolio = portfolio.model_copy(
                update={
                    "status": PortfolioStatus.DRAFT,
                    "hypotheses": tuple(
                        h.model_copy(
                            update={
                                "status": HypothesisStatus.DRAFT,
                                "semantic_review_ref": review_id,
                            }
                        )
                        for h in portfolio.hypotheses
                    ),
                }
            )
        if portfolio.generated_from_head_set != head_digest:
            raise ValueError("hypothesis portfolio was generated from a different head set")
        if (
            previous_portfolio is not None
            and portfolio.portfolio_id != previous_portfolio.portfolio_id
        ):
            raise ValueError("reanalysis must preserve the existing hypothesis portfolio ID")
        if self._full_memory is not None and memory_basis is not None:
            self._full_memory.require_authority(memory_basis)
        hypothesis_batch = (
            None
            if work is None
            else self._research.stage_hypotheses(portfolio, actor=command.actor, context=research)
        )
        hypothesis_review = None
        if work is not None:
            assert hypothesis_batch is not None
            hypothesis_review, context = await self._review_hypotheses(
                command, context, portfolio, hypothesis_batch, work
            )
        action_result, action_compilation, semantic_repair_attempted = await self._plan_actions(
            command, context, portfolio, memory_basis
        )
        plan = require_compiled_behavior_plan(action_compilation.plan)
        if work is not None:
            from thoth.application.services.research_decision_gates import qualify_action_frontier

            plan = qualify_action_frontier(plan, work)
        if work is not None and work.show_draft is not None:
            work.show_draft(
                "ACTION_COMPARISON",
                {
                    "state": "DRAFT_NOT_COMMITTED",
                    "action_plan": plan.model_dump(mode="json"),
                    "hypothesis_review": work.context.get("hypothesis_review"),
                },
            )
        if plan.plan_revision_digest != head_digest:
            raise ValueError("action plan was generated from a different head set")
        if previous_action_plan is not None and plan.plan_id != previous_action_plan.plan_id:
            raise ValueError("reanalysis must preserve the existing action plan ID")
        research_batch = (
            self._research.stage_cycle(portfolio, plan, actor=command.actor, context=research)
            if hypothesis_batch is None
            else ResearchOwnershipBatch(
                hypothesis_batch,
                self._research.stage_actions(
                    plan,
                    actor=command.actor,
                    context=research,
                    hypotheses=hypothesis_batch.hypotheses,
                ),
            )
        )
        staged_items = [
            self._stage(
                command,
                EntityType.EVIDENCE,
                assessment.assessment_id,
                assessment.model_dump(mode="python"),
                command.evidence,
                heads,
            ),
            research_batch.hypotheses.staged[0],
            research_batch.actions.staged[0],
        ]
        outcome = None
        if previous_portfolio is not None or previous_action_plan is not None:
            outcome = self._reanalysis_outcome(command, plan, head_digest)
            staged_items.append(
                self._stage(
                    command,
                    EntityType.OUTCOME,
                    outcome.outcome_id,
                    outcome.model_dump(mode="python"),
                    command.evidence,
                    heads,
                )
            )
        staged_items.extend(criterion_staged)
        if hypothesis_review is not None and review_id is not None and work is not None:
            assert hypothesis_batch is not None
            staged_items.append(
                self._stage(
                    command,
                    EntityType.EVIDENCE,
                    review_id,
                    HypothesisSemanticReviewRecord(
                        request_ref=work.request_ref,
                        target_revision_digests=tuple(
                            h.revision_digest for h in hypothesis_batch.hypotheses
                        ),
                        portfolio_revision_digest=hypothesis_batch.portfolio.revision_digest,
                        review=hypothesis_review,
                    ).model_dump(mode="python"),
                    command.evidence,
                    heads,
                )
            )
        staged = (*staged_items, *research_batch.additional_staged)
        staged_keys = {
            f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged
        }
        expected_heads = {key: value for key, value in heads.items() if key in staged_keys}
        expected_heads.update(command.expected_head_overrides or {})
        commit, memories, full_memory_result = await self._commit_cycle(
            command=command,
            heads=heads,
            expected_heads=expected_heads,
            staged=staged,
            assessment=assessment,
            portfolio=portfolio,
            plan=plan,
            outcome=outcome,
            memory_basis=memory_basis,
            research_batch=research_batch,
        )
        return ThreadCycleResult(
            assessment=assessment,
            portfolio=portfolio,
            action_plan=plan,
            action_compilation=action_compilation,
            outcome=outcome,
            commit=commit,
            memory_ids=tuple(record.memory_id for record in memories),
            model_ids=(hypothesis_result.model_id, action_result.model_id),
            full_memory=full_memory_result,
            semantic_repair_attempted=semantic_repair_attempted,
        )

    def _reanalysis_outcome(
        self, command: ThreadCycleCommand, plan: ActionPlan, head_digest: str
    ) -> OutcomeRecord:
        return OutcomeRecord(
            outcome_id=f"outcome:{command.thread_id}",
            project_id=command.project_id,
            action_id=plan.frontier[0] if plan.frontier else plan.alternatives[0].action_id,
            status=OutcomeStatus.OBSERVED,
            observed_evidence_refs=tuple(span.span_id for span in command.evidence),
            hypothesis_updates=(),
            interpretation=(
                "Reanalysis was recorded after connected evidence changed; causal attribution "
                "has not been independently established."
            ),
            limitations=("This outcome record does not certify causality or scientific truth.",),
            recorded_at=self._clock.now(),
            input_head_set_digest=head_digest,
        )

    async def _review_hypotheses(
        self,
        command: ThreadCycleCommand,
        context: ContextPack,
        portfolio: HypothesisPortfolio,
        hypothesis_batch: HypothesisOwnershipBatch,
        work: ResearchWork,
    ) -> tuple[HypothesisSemanticReview, ContextPack]:
        assert hypothesis_batch is not None
        if work.show_draft is not None:
            work.show_draft(
                "HYPOTHESIS_REVIEW",
                {
                    "state": "DRAFT_NOT_COMMITTED",
                    "portfolio": portfolio.model_dump(mode="json"),
                    "proposed_revision_refs": [
                        h.revision_digest for h in hypothesis_batch.hypotheses
                    ],
                },
            )
        review_result = await self._model.structured(
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

    async def _plan_actions(
        self,
        command: ThreadCycleCommand,
        context: ContextPack,
        portfolio: HypothesisPortfolio,
        memory_basis: MemoryPreparationBasis | None,
    ) -> tuple[ModelResult[ActionPlanDraft], ActionPlanCompilationResult, bool]:
        action_result = await self._model.structured(
            ModelRequest(
                role=ModelRole.ACTION_PLANNER,
                project_id=command.project_id,
                cutoff_at=command.cutoff_at,
                context_pack=context.model_copy(update={"candidate_portfolio": portfolio}),
                output_model=ActionPlanDraft,
                prompt_version="action_alternatives.v2",
                model_policy_ref=command.model_policy_ref,
                max_output_tokens=ACTION_PLAN_MAX_OUTPUT_TOKENS,
            )
        )
        semantic_repair_attempted = False
        action_compilation = compile_action_plan(
            action_result.output,
            evidence_ids=frozenset(span.span_id for span in command.evidence),
            hypothesis_ids=frozenset(
                hypothesis.hypothesis_id for hypothesis in portfolio.hypotheses
            ),
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
            if self._full_memory is not None and memory_basis is not None:
                self._full_memory.require_authority(memory_basis)
            repair_result = await self._model.structured(
                ModelRequest(
                    role=ModelRole.ACTION_PLANNER,
                    project_id=command.project_id,
                    cutoff_at=command.cutoff_at,
                    context_pack=repair_context.model_copy(
                        update={"candidate_portfolio": portfolio}
                    ),
                    output_model=ActionPlanDraft,
                    prompt_version="action_alternatives.semantic_repair.v2",
                    model_policy_ref=command.model_policy_ref,
                    max_output_tokens=ACTION_PLAN_MAX_OUTPUT_TOKENS,
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

    async def _commit_cycle(
        self,
        *,
        command: ThreadCycleCommand,
        heads: dict[str, str],
        expected_heads: dict[str, str],
        staged: tuple[StagedRevision, ...],
        assessment: InformationSufficiencyAssessment,
        portfolio: HypothesisPortfolio,
        plan: ActionPlan,
        outcome: OutcomeRecord | None,
        memory_basis: MemoryPreparationBasis | None,
        research_batch: ResearchOwnershipBatch,
    ) -> tuple[CommitResult, tuple[MemoryRecord, ...], FullMemoryPromotionResult | None]:
        full_memory_result: FullMemoryPromotionResult | None = None
        memories = self._prepare_memory(command.project_id, staged)
        prepared = None
        if self._full_memory is not None and memory_basis is not None:
            # Preserve semantic candidates as a branch; admit no stale memory.
            with suppress(MemoryPreparationHeadChanged):
                prepared = await self._full_memory.prepare_thread_results(
                    basis=memory_basis,
                    thread_id=command.thread_id,
                    candidates=memories,
                    staged_revisions=staged,
                )
        with (
            self._ledger.transaction(),
            self._unit_of_work.transaction() if self._unit_of_work is not None else nullcontext(),
        ):
            check_research_boundary()
            if self._full_memory is not None and memory_basis is not None:
                self._full_memory.require_authority(memory_basis)
            commit = self._commits.commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=command.project_id,
                    expected_heads=expected_heads,
                    expected_head_set_digest=head_set_digest(heads),
                    staged_revisions=staged,
                    impact_plan=ImpactPropagationPlan(),
                    actor=command.actor,
                    reason=f"thread cycle {command.thread_id}",
                )
            )
            hypothesis_ref = f"HYPOTHESIS:{portfolio.portfolio_id}"
            action_ref = f"ACTION:{plan.plan_id}"
            outcome_ref = None if outcome is None else f"OUTCOME:{outcome.outcome_id}"
            if commit.disposition == CommitDisposition.FAST_FORWARD:
                self._research.persist_cycle(research_batch)
                self._record_dependencies(
                    command=command,
                    assessment=assessment,
                    hypothesis_ref=hypothesis_ref,
                    action_ref=action_ref,
                    outcome_ref=outcome_ref,
                    staged=staged,
                )
                if self._full_memory is None:
                    for record in memories:
                        self._memory.add(record)
                elif prepared is None:
                    raise ValueError("MEMORY_PREPARATION_REQUIRED")
                else:
                    full_memory_result = self._full_memory.commit_prepared(
                        prepared,
                        expected_head=commit.after_head_set_digest,
                    )
            else:
                memories = ()
        if commit.disposition == CommitDisposition.FAST_FORWARD:
            from thoth.application.services.research_basis_capture import capture_cycle_produced

            capture_cycle_produced(staged, commit.receipt.receipt_id)
        return commit, memories, full_memory_result

    def _record_dependencies(
        self,
        *,
        command: ThreadCycleCommand,
        assessment: InformationSufficiencyAssessment,
        hypothesis_ref: str,
        action_ref: str,
        outcome_ref: str | None,
        staged: tuple[StagedRevision, ...],
    ) -> None:
        self._dependencies.add(
            DependencyRelation(
                relation_id=self._ids.new("relation"),
                project_id=command.project_id,
                source_ref=f"EVIDENCE:{assessment.assessment_id}",
                relation_type="DERIVES",
                target_ref=hypothesis_ref,
                revision_digest=staged[1].revision.revision_digest,
            )
        )
        self._dependencies.add(
            DependencyRelation(
                relation_id=self._ids.new("relation"),
                project_id=command.project_id,
                source_ref=hypothesis_ref,
                relation_type="DERIVES",
                target_ref=action_ref,
                revision_digest=staged[2].revision.revision_digest,
            )
        )
        if outcome_ref is not None:
            self._dependencies.add(
                DependencyRelation(
                    relation_id=self._ids.new("relation"),
                    project_id=command.project_id,
                    source_ref=action_ref,
                    relation_type="DERIVES",
                    target_ref=outcome_ref,
                    revision_digest=staged[3].revision.revision_digest,
                )
            )
        work = research_work.get()
        if work is not None and work.context.get("answer_status") == "PARTIAL_HOLD":
            return
        self._dependencies.mark_current(
            command.project_id,
            tuple(
                f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged
            ),
            caused_by_revision=staged[-1].revision.revision_digest,
            updated_at=self._clock.now().isoformat(),
        )

    @staticmethod
    def _build_model_context(
        command: ThreadCycleCommand,
        assessment: InformationSufficiencyAssessment,
        head_digest: str,
        research: ResearchContext,
    ) -> ContextPack:
        return ContextPack(
            case_id=command.case_id,
            project_id=command.project_id,
            object_id=command.object_id,
            problem=command.problem,
            evidence=command.evidence,
            criteria=command.criteria,
            sufficiency=assessment,
            input_head_set_digest=head_digest,
            policy_hints={
                "minimum_action_tier_by_family": {
                    family: tier.value
                    for family, tier in command.action_policy.minimum_tier_by_family.items()
                },
                "approver_role_by_family": command.action_policy.approver_role_by_family,
                "unknown_family_tier": command.action_policy.unknown_family_tier.value,
            },
            research_context={
                "entity_currentness": {
                    key: value.model_dump(mode="json")
                    for key, value in research.currentness.items()
                }
            },
            previous_portfolio=research.portfolio_view,
            previous_action_plan=research.action_plan_view,
            canonical_hypotheses=research.hypotheses,
            canonical_portfolio=research.portfolio,
            canonical_actions=research.actions,
            canonical_action_plan=research.action_plan,
            canonical_test_assessments=research.test_assessments,
            unresolved_research_refs=research.unrepresented_refs,
        )

    def _stage_criteria(
        self, command: ThreadCycleCommand, heads: dict[str, str]
    ) -> tuple[StagedRevision, ...]:
        staged: list[StagedRevision] = []
        for candidate in command.criteria:
            if candidate.project_id != command.project_id:
                raise ResearchIdentityError("CRITERION_PROJECT_SCOPE_MISMATCH")
            head = heads.get(f"CRITERION:{candidate.criterion_id}")
            if candidate.source_contract_revision is not None:
                if head != candidate.source_contract_revision:
                    raise ResearchIdentityError("CRITERION_OWNER_REVISION_CHANGED")
                revision = self._ledger.read_revision_by_digest(
                    command.project_id, candidate.source_contract_revision
                )
                snapshot = (
                    None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
                )
                if revision is None or snapshot is None:
                    raise ResearchIdentityError("CRITERION_OWNER_MISSING")
                record = decode_research(revision, snapshot).record
                if (
                    not isinstance(record, CriterionContractRecord)
                    or criterion_projection(record) != candidate
                ):
                    raise ResearchIdentityError("CRITERION_OWNER_VIEW_MISMATCH")
                from thoth.application.services.research_basis_capture import capture_consumed

                capture_consumed(
                    command.project_id,
                    f"CRITERION:{candidate.criterion_id}",
                    revision.revision_digest,
                )
                continue
            if head is not None:
                revision = self._ledger.read_revision_by_digest(command.project_id, head)
                snapshot = (
                    None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
                )
                if revision is None or snapshot is None:
                    raise ResearchIdentityError("CRITERION_OWNER_MISSING")
                if not isinstance(decode_research(revision, snapshot).record, CriterionCandidate):
                    raise ResearchIdentityError("CRITERION_OWNER_BINDING_REQUIRED")
            staged.append(
                self._stage(
                    command,
                    EntityType.CRITERION,
                    candidate.criterion_id,
                    candidate.model_dump(mode="python"),
                    command.evidence,
                    heads,
                )
            )
        return tuple(staged)

    def _stage(
        self,
        command: ThreadCycleCommand,
        entity_type: EntityType,
        entity_id: str,
        content: dict[str, object],
        evidence: tuple[EvidenceSpan, ...],
        heads: dict[str, str],
    ) -> StagedRevision:
        schema_version = str(content.get("schema_version", "1.0.0"))
        content_digest = domain_digest("SNAPSHOT", schema_version, canonical_payload(content))
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=command.project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            schema_version=schema_version,
            content=content,
            content_digest=content_digest,
        )
        aggregate_key = f"{entity_type.value}:{entity_id}"
        current = heads.get(aggregate_key)
        revision_id = self._ids.new("revision")
        created_at = self._clock.now()
        revision_payload: dict[str, object] = {
            "revision_id": revision_id,
            "project_id": command.project_id,
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "snapshot_id": snapshot.snapshot_id,
            "parent_revision_digests": [] if current is None else [current],
            "actor": command.actor,
            "reason": f"thread cycle {command.thread_id}",
            "evidence_refs": [span.span_id for span in evidence],
            "affected_refs": [],
            "created_at": created_at,
            "schema_version": schema_version,
        }
        revision = SemanticRevision.model_validate(
            {
                **revision_payload,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION", schema_version, canonical_payload(revision_payload)
                ),
            }
        )
        return StagedRevision(snapshot=snapshot, revision=revision)

    def _prepare_memory(
        self,
        project_id: str,
        staged: tuple[StagedRevision, ...],
    ) -> tuple[MemoryRecord, ...]:
        from thoth.application.services.domain_reference_memory import domain_reference_memory

        return tuple(
            record
            for item in staged
            if (record := domain_reference_memory(item.revision, self._ids.new("memory")))
            is not None
        )
