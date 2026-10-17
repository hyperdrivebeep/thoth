from __future__ import annotations

from contextlib import nullcontext

from thoth.application.reducers import (
    assess_information_sufficiency,
)
from thoth.application.services.behavior_context import (
    require_compiled_behavior_plan,
)
from thoth.application.services.cycle_memory_preparation import prepare_cycle_memory
from thoth.application.services.full_project_memory import (
    FullMemoryPromotionResult,
    FullProjectMemoryService,
)
from thoth.application.services.hypothesis_contract_v3 import generator_contract_for_work
from thoth.application.services.hypothesis_table_filler import accepted_portfolio
from thoth.application.services.memory_relation_resolver import MemoryRelationBudget
from thoth.application.services.research_commit_scope import ReadScope, read_scope
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
from thoth.application.workflows import thread_cycle_model_phases as model_phases
from thoth.application.workflows import thread_cycle_staging as cycle_staging
from thoth.application.workflows.thread_cycle_contracts import (
    ThreadCycleBranchResult as ThreadCycleBranchResult,
)
from thoth.application.workflows.thread_cycle_contracts import (
    ThreadCycleCommand as ThreadCycleCommand,
)
from thoth.application.workflows.thread_cycle_contracts import (
    ThreadCycleResult as ThreadCycleResult,
)
from thoth.application.workflows.thread_cycle_preparation import (
    build_model_context,
    draft_portfolio,
    expected_cycle_heads,
)
from thoth.domain.action import (
    ActionPlan,
    ActionPlanCompilationResult,
    ActionPlanDraft,
)
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import (
    EntityType,
    ModelRole,
    SufficiencyStatus,
)
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.evidence_requirements import (
    HypothesisSemanticReview,
    HypothesisSemanticReviewRecord,
)
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.memory import MemoryRecord
from thoth.domain.memory_preparation import (
    MemoryPreparationBasis,
    MemoryPreparationStale,
    PreparedMemoryPromotion,
)
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.relation import DependencyRelation
from thoth.domain.research_execution import ResearchWork, check_research_boundary, research_work
from thoth.domain.revision import (
    ImpactPropagationPlan,
    RevisionChangeSet,
    StagedRevision,
)
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import MemoryStorePort
from thoth.ports.model import ModelPort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort

ACTION_PLAN_MAX_OUTPUT_TOKENS = 12_000


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
                **generator_contract_for_work(),
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
        portfolio = await accepted_portfolio(
            self._model, hypothesis_result.output, context, command, require_unknown
        )
        review_id = self._ids.new("hypothesis-semantic-review") if work is not None else None
        if work is not None:
            # A generator cannot grant epistemic or empirical promotion to itself.
            portfolio = draft_portfolio(portfolio, review_id)
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
        expected_heads = expected_cycle_heads(heads, staged, command.expected_head_overrides)
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
        return cycle_staging.reanalysis_outcome(command, plan, head_digest, clock=self._clock)

    async def _review_hypotheses(
        self,
        command: ThreadCycleCommand,
        context: ContextPack,
        portfolio: HypothesisPortfolio,
        hypothesis_batch: HypothesisOwnershipBatch,
        work: ResearchWork,
    ) -> tuple[HypothesisSemanticReview, ContextPack]:
        return await model_phases.review_hypotheses(
            command, context, portfolio, hypothesis_batch, work, model=self._model
        )

    async def _plan_actions(
        self,
        command: ThreadCycleCommand,
        context: ContextPack,
        portfolio: HypothesisPortfolio,
        memory_basis: MemoryPreparationBasis | None,
    ) -> tuple[ModelResult[ActionPlanDraft], ActionPlanCompilationResult, bool]:
        return await model_phases.plan_actions(
            command,
            context,
            portfolio,
            memory_basis,
            model=self._model,
            full_memory=self._full_memory,
            max_output_tokens=ACTION_PLAN_MAX_OUTPUT_TOKENS,
        )

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
        memories = self._prepare_memory(command.project_id, staged)
        # With a research attempt on record, compare what it read and wrote, not every head.
        scope = read_scope(
            research_work.get(),
            command.project_id,
            heads,
            (f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged),
        )
        if scope is not None:
            expected_heads = {**scope.expected_heads, **expected_heads}
        attempt = 1
        relations = MemoryRelationBudget()
        while True:
            try:
                prepared = await prepare_cycle_memory(
                    self._full_memory,
                    thread_id=command.thread_id,
                    basis=memory_basis,
                    scope=scope,
                    expected_heads=expected_heads,
                    candidates=memories,
                    staged=staged,
                    attempt=attempt,
                    relations=relations,
                )
                commit, memories, full_memory_result = self._save_cycle(
                    command=command,
                    heads=heads,
                    expected_heads=expected_heads,
                    scope=scope,
                    staged=staged,
                    assessment=assessment,
                    portfolio=portfolio,
                    plan=plan,
                    outcome=outcome,
                    memory_basis=memory_basis,
                    research_batch=research_batch,
                    memories=memories,
                    prepared=prepared,
                )
                break
            except MemoryPreparationStale:
                # Memory was added after the review (a user correction, another investigation).
                # The save was rolled back whole, so one fresh review and save cannot duplicate it.
                if attempt >= 2:
                    raise
                attempt += 1
        if commit.disposition == CommitDisposition.FAST_FORWARD:
            from thoth.application.services.research_basis_capture import capture_cycle_produced

            capture_cycle_produced(staged, commit.receipt.receipt_id)
        return commit, memories, full_memory_result

    def _save_cycle(
        self,
        *,
        command: ThreadCycleCommand,
        heads: dict[str, str],
        expected_heads: dict[str, str],
        scope: ReadScope | None,
        staged: tuple[StagedRevision, ...],
        assessment: InformationSufficiencyAssessment,
        portfolio: HypothesisPortfolio,
        plan: ActionPlan,
        outcome: OutcomeRecord | None,
        memory_basis: MemoryPreparationBasis | None,
        research_batch: ResearchOwnershipBatch,
        memories: tuple[MemoryRecord, ...],
        prepared: PreparedMemoryPromotion | None,
    ) -> tuple[CommitResult, tuple[MemoryRecord, ...], FullMemoryPromotionResult | None]:
        """One transaction: the ledger commit, the research records and the prepared memory."""

        full_memory_result: FullMemoryPromotionResult | None = None
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
                    expected_absent_heads=() if scope is None else scope.expected_absent,
                    expected_head_set_digest=None if scope is not None else head_set_digest(heads),
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
        return build_model_context(command, assessment, head_digest, research)

    def _stage_criteria(
        self, command: ThreadCycleCommand, heads: dict[str, str]
    ) -> tuple[StagedRevision, ...]:
        return cycle_staging.stage_criteria(
            command,
            heads,
            ledger=self._ledger,
            stage=lambda cmd, entity, identifier, content, evidence, current_heads: self._stage(
                cmd, entity, identifier, content, evidence, current_heads
            ),
        )

    def _stage(
        self,
        command: ThreadCycleCommand,
        entity_type: EntityType,
        entity_id: str,
        content: dict[str, object],
        evidence: tuple[EvidenceSpan, ...],
        heads: dict[str, str],
    ) -> StagedRevision:
        return cycle_staging.stage_revision(
            command,
            entity_type,
            entity_id,
            content,
            evidence,
            heads,
            ids=self._ids,
            clock=self._clock,
        )

    def _prepare_memory(
        self,
        project_id: str,
        staged: tuple[StagedRevision, ...],
    ) -> tuple[MemoryRecord, ...]:
        return cycle_staging.prepare_memory(project_id, staged, ids=self._ids)
