from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from thoth.application.services.r2_execution_projections import (
    admission_policy_projection,
    completed_projection,
    failure_projection,
    hold_projection,
    hold_reason_code,
    prepare_blocked_projection,
    sandbox_failure_projection,
    stale_head_projection,
    template_required_projection,
)
from thoth.application.services.r2_outcome_projection import (
    ProcessOutcomeProjection,
    build_process_outcome,
)
from thoth.application.services.r2_recovery_records import (
    assessed_outcome,
    closed_loop_actor,
    failure_class_of,
    r2_action,
    reconciliation_record,
    recovery_actor,
    recovery_attempt,
    recovery_candidate,
    repair_basis_text,
    stage_revision,
    terminal_state,
)
from thoth.application.services.r2_sandbox_compiler import (
    R2SandboxSpecCompiler,
    SandboxTemplateRequired,
)
from thoth.application.services.r2_test_lifecycle import (
    PreparedResearchExecution,
    R2TestLifecycle,
    ResearchTestCompletion,
)
from thoth.application.services.research_identity_service import (
    ResearchIdentityService,
    ResearchOwnershipBatch,
)
from thoth.application.services.revision_service import (
    CommitDisposition,
    CommitResult,
    RevisionCommitService,
)
from thoth.application.services.sandbox_service import SandboxExecutionBundle, SandboxService
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, head_set_digest
from thoth.domain.enums import (
    EntityType,
)
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.policy import AuthoritativeExecutionPolicy, R2RecoveryPolicy
from thoth.domain.post_execution_learning import CommittedExecutionBasis
from thoth.domain.project import Project, WorkThread
from thoth.domain.recovery import (
    ExecutionFailureClass,
    RecoveryAttempt,
)
from thoth.domain.research_execution import check_research_boundary, research_work
from thoth.domain.research_request import RevisionRef
from thoth.domain.revision import (
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.domain.sandbox import (
    SandboxErrorCode,
    SandboxFailure,
    SandboxRunSpec,
)
from thoth.domain.test_validity import TestValidityAssessment
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


@dataclass(frozen=True)
class R2ClosedLoopExecution:
    projection: dict[str, JsonValue]
    portfolio: HypothesisPortfolio | None = None
    action_plan: ActionPlan | None = None
    committed_basis: CommittedExecutionBasis | None = None


class R2ClosedLoopCoordinator:
    def __init__(
        self,
        *,
        compiler: R2SandboxSpecCompiler,
        sandbox: SandboxService,
        ledger: LedgerPort,
        research: ResearchIdentityService,
        policies: ProjectPolicyReaderPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        test_lifecycle: R2TestLifecycle | None = None,
    ) -> None:
        self._compiler = compiler
        self._sandbox = sandbox
        self._ledger = ledger
        self._research = research
        self._policies = policies
        self._clock = clock
        self._ids = ids
        self._test_lifecycle = test_lifecycle

    async def execute_if_required(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
        action_plan: ActionPlan,
    ) -> R2ClosedLoopExecution | None:
        action = r2_action(action_plan)
        if action is None:
            return None
        spec, compile_hold = self._compile_or_hold(project, action_plan, action)
        if compile_hold is not None:
            return compile_hold
        assert spec is not None
        prepared_test, preparation_hold = self._prepare_test(project, portfolio, action, spec)
        if preparation_hold is not None:
            return preparation_hold
        if prepared_test is not None:
            spec, portfolio, action_plan = (
                prepared_test.spec,
                prepared_test.portfolio,
                prepared_test.action_plan,
            )
        sealed_heads = dict(self._ledger.read_heads(project.project_id))
        sealed_head_digest = head_set_digest(sealed_heads)
        research = self._research.context(project.project_id, portfolio.object_id, sealed_heads)
        recovery_policy = self._recovery_policy(project.project_id)
        attempts: list[RecoveryAttempt] = []
        retry_ordinal = 0
        failure_completion: ResearchTestCompletion | None = None
        while True:
            bundle, run_hold = await self._run_or_hold(spec, action.action_id)
            if run_hold is not None:
                return run_hold
            assert bundle is not None
            if head_set_digest(self._ledger.read_heads(project.project_id)) != sealed_head_digest:
                self._record_test_hold(prepared_test, bundle, "SANDBOX_HEAD_STALE_AFTER_IO")
                return self._stale_head_execution(
                    project.project_id,
                    portfolio,
                    action_plan,
                    action.action_id,
                    bundle,
                )
            failure_class = failure_class_of(bundle, recovery_policy)
            assessments, assessment_hold = self._measurement_assessments(prepared_test, bundle)
            if assessment_hold is not None:
                return assessment_hold
            if failure_class is not None:
                try:
                    with self._sandbox.accepting_observation(spec, bundle):
                        if prepared_test is not None and self._test_lifecycle is not None:
                            failure_completion = self._test_lifecycle.complete(
                                prepared_test, bundle, assessments
                            )
                except SandboxFailure as exc:
                    self._record_test_hold(prepared_test, bundle, exc.code.value)
                    return self._admission_hold(
                        project.project_id, portfolio, action_plan, action.action_id, bundle, exc
                    )
            attempts.append(recovery_attempt(spec, bundle, failure_class, retry_ordinal))
            if failure_class is None:
                break
            if (
                failure_class == ExecutionFailureClass.TRANSIENT_INFRA
                and retry_ordinal < recovery_policy.max_transient_retries
            ):
                retry_ordinal += 1
                if prepared_test is not None and self._test_lifecycle is not None:
                    try:
                        prepared_test = self._test_lifecycle.retry(project, action, prepared_test)
                    except ValueError as exc:
                        return self._test_hold(exc, bundle=bundle)
                    spec, portfolio, action_plan = (
                        prepared_test.spec,
                        prepared_test.portfolio,
                        prepared_test.action_plan,
                    )
                    sealed_heads = dict(self._ledger.read_heads(project.project_id))
                    sealed_head_digest = head_set_digest(sealed_heads)
                    research = self._research.context(
                        project.project_id, portfolio.object_id, sealed_heads
                    )
                else:
                    spec = spec.model_copy(update={"attempt_id": self._ids.new("r2-attempt")})
                continue
            return self._failure_execution(
                project=project,
                action_plan=action_plan,
                action_id=action.action_id,
                spec=spec,
                failure_class=failure_class,
                attempts=tuple(attempts),
                expected_heads=sealed_heads,
                expected_head_set_digest=sealed_head_digest,
                retry_budget_exhausted=(
                    failure_class == ExecutionFailureClass.TRANSIENT_INFRA
                    and retry_ordinal >= recovery_policy.max_transient_retries
                ),
                test_completion=failure_completion,
            )
        process = build_process_outcome(
            project=project,
            portfolio=portfolio,
            action_plan=action_plan,
            action=action,
            spec=spec,
            bundle=bundle,
            sealed_head_digest=sealed_head_digest,
            clock=self._clock,
            ids=self._ids,
        )
        outcome = process.outcome
        if prepared_test is not None:
            outcome = assessed_outcome(outcome, assessments)
        actor = closed_loop_actor()
        research_batch = self._research.stage_cycle(
            process.portfolio, process.plan, actor=actor, context=research
        )
        staged = (
            research_batch.hypotheses.staged[0],
            research_batch.actions.staged[0],
            stage_revision(
                self._ids,
                self._clock,
                project.project_id,
                EntityType.OUTCOME,
                outcome.outcome_id,
                outcome.model_dump(mode="python"),
                sealed_heads,
            ),
        )
        staged = (*staged, *research_batch.additional_staged)
        keys = {f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged}
        expected_heads = {key: value for key, value in sealed_heads.items() if key in keys}
        try:
            committed = self._commit_outcome(
                project.project_id,
                action.action_id,
                spec,
                bundle,
                staged,
                actor,
                expected_heads,
                sealed_head_digest,
                research_batch,
                prepared_test,
                assessments,
            )
        except SandboxFailure as exc:
            self._record_test_hold(prepared_test, bundle, exc.code.value)
            return self._admission_hold(
                project.project_id, portfolio, action_plan, action.action_id, bundle, exc
            )
        if isinstance(committed, R2ClosedLoopExecution):
            return committed
        commit, test_completion = committed
        return self._completed_execution(
            project,
            process,
            outcome,
            commit,
            test_completion,
            spec,
            bundle,
            tuple(attempts),
            retry_ordinal,
            staged,
            assessments,
        )

    def _completed_execution(
        self,
        project: Project,
        process: ProcessOutcomeProjection,
        outcome: OutcomeRecord,
        commit: CommitResult,
        test_completion: ResearchTestCompletion | None,
        spec: SandboxRunSpec,
        bundle: SandboxExecutionBundle,
        attempts: tuple[RecoveryAttempt, ...],
        retry_ordinal: int,
        staged: tuple[StagedRevision, ...],
        assessments: tuple[TestValidityAssessment, ...],
    ) -> R2ClosedLoopExecution:
        return R2ClosedLoopExecution(
            projection=completed_projection(
                test_completion, spec, bundle, process, outcome, commit, attempts, retry_ordinal
            ),
            portfolio=process.portfolio,
            action_plan=process.plan,
            committed_basis=CommittedExecutionBasis(
                execution_attempt_ref=spec.attempt_id,
                outcome_revision_ref=self._revision_ref(staged[2].revision),
                updated_revision_refs=self._current_research_refs(project.project_id, staged),
                assessments=assessments,
                observation_refs=process.observation_refs,
            ),
        )

    @staticmethod
    def _revision_ref(revision: SemanticRevision) -> RevisionRef:
        return RevisionRef(
            project_id=revision.project_id,
            entity_type=revision.entity_type.value,
            entity_id=revision.entity_id,
            revision_id=revision.revision_id,
            revision_digest=revision.revision_digest,
        )

    def _current_research_refs(
        self,
        project: str,
        staged: tuple[StagedRevision, ...],
    ) -> tuple[RevisionRef, ...]:
        heads = self._ledger.read_heads(project)
        refs: dict[str, RevisionRef] = {}
        for item in staged:
            revision = item.revision
            if revision.entity_type not in {EntityType.HYPOTHESIS, EntityType.ACTION}:
                continue
            digest = heads.get(f"{revision.entity_type.value}:{revision.entity_id}")
            current = (
                None if digest is None else self._ledger.read_revision_by_digest(project, digest)
            )
            if current is not None:
                refs[current.revision_digest] = self._revision_ref(current)
        return tuple(refs.values())

    def _prepare_test(
        self,
        project: Project,
        portfolio: HypothesisPortfolio,
        action: ActionCandidate,
        spec: SandboxRunSpec,
    ) -> tuple[PreparedResearchExecution | None, R2ClosedLoopExecution | None]:
        if self._test_lifecycle is None:
            return None, None
        try:
            return self._test_lifecycle.prepare(project, portfolio, action, spec), None
        except SandboxFailure as exc:
            return None, R2ClosedLoopExecution(projection=prepare_blocked_projection(exc))
        except ValueError as exc:
            return None, self._test_hold(exc)

    def _measurement_assessments(
        self,
        prepared: PreparedResearchExecution | None,
        bundle: SandboxExecutionBundle,
    ) -> tuple[tuple[TestValidityAssessment, ...], R2ClosedLoopExecution | None]:
        if prepared is None or self._test_lifecycle is None:
            return (), None
        try:
            return self._test_lifecycle.prepare_assessments(prepared, bundle), None
        except ValueError as exc:
            self._sandbox.reject_observation(bundle, reason="RESEARCH_TEST_ASSESSMENT_REJECTED")
            self._record_test_hold(prepared, bundle, "RESEARCH_TEST_ASSESSMENT_REJECTED")
            return (), self._test_hold(exc, bundle=bundle)

    def _record_test_hold(
        self,
        prepared: PreparedResearchExecution | None,
        bundle: SandboxExecutionBundle,
        reason: str,
    ) -> None:
        if prepared is not None and self._test_lifecycle is not None:
            self._test_lifecycle.hold(prepared, bundle, reason)

    @staticmethod
    def _test_hold(
        error: ValueError, *, bundle: SandboxExecutionBundle | None = None
    ) -> R2ClosedLoopExecution:
        code = hold_reason_code(error)
        return R2ClosedLoopExecution(projection=hold_projection(code, error, bundle))

    def _compile_or_hold(
        self,
        project: Project,
        plan: ActionPlan,
        action: ActionCandidate,
    ) -> tuple[SandboxRunSpec | None, R2ClosedLoopExecution | None]:
        try:
            return self._compiler.compile(project=project, plan=plan, action=action), None
        except SandboxTemplateRequired:
            return None, R2ClosedLoopExecution(
                projection=template_required_projection(action.action_id)
            )

        except SandboxFailure as exc:
            return None, R2ClosedLoopExecution(
                projection=sandbox_failure_projection(exc, action.action_id)
            )

    def _commit_outcome(
        self,
        project_id: str,
        action_id: str,
        spec: SandboxRunSpec,
        bundle: SandboxExecutionBundle,
        staged: tuple[StagedRevision, ...],
        actor: ActorRef,
        expected_heads: dict[str, str],
        sealed_head_digest: str,
        research_batch: ResearchOwnershipBatch,
        prepared_test: PreparedResearchExecution | None = None,
        assessments: tuple[TestValidityAssessment, ...] = (),
    ) -> tuple[CommitResult, ResearchTestCompletion | None] | R2ClosedLoopExecution:
        try:
            with self._sandbox.accepting_observation(spec, bundle):
                commit = RevisionCommitService(
                    self._ledger,
                    self._clock,
                    self._ids,
                    policy_version=spec.policy_digest,
                ).commit(
                    RevisionChangeSet(
                        changeset_id=self._ids.new("changeset"),
                        project_id=project_id,
                        expected_heads=expected_heads,
                        expected_head_set_digest=sealed_head_digest,
                        staged_revisions=staged,
                        impact_plan=ImpactPropagationPlan(),
                        actor=actor,
                        reason=f"R2 closed loop for {action_id}",
                    )
                )
                if commit.disposition == CommitDisposition.BRANCH:
                    raise SandboxFailure(
                        SandboxErrorCode.INPUT_INVALID, "R2 acceptance HeadSet changed"
                    )
                self._research.persist_cycle(research_batch)
                if prepared_test is not None:
                    if self._test_lifecycle is None:
                        raise ValueError("RESEARCH_TEST_LIFECYCLE_UNAVAILABLE")
                    completed = self._test_lifecycle.complete(prepared_test, bundle, assessments)
                    return completed.commit, completed
                return commit, None
        except SandboxFailure:
            raise
        except Exception:
            if prepared_test is None:
                raise
            self._sandbox.reject_observation(bundle, reason="RESEARCH_TEST_FINALIZATION_FAILED")
            self._record_test_hold(prepared_test, bundle, "RESEARCH_TEST_FINALIZATION_FAILED")
            return self._test_hold(ValueError("RESEARCH_TEST_FINALIZATION_FAILED"), bundle=bundle)

    def _admission_hold(
        self,
        project_id: str,
        portfolio: HypothesisPortfolio,
        action_plan: ActionPlan,
        action_id: str,
        bundle: SandboxExecutionBundle,
        failure: SandboxFailure,
    ) -> R2ClosedLoopExecution:
        if failure.policy_denial is not None:
            return R2ClosedLoopExecution(
                projection=admission_policy_projection(action_id, failure.policy_denial, bundle)
            )
        return self._stale_head_execution(
            project_id,
            portfolio,
            action_plan,
            action_id,
            bundle,
            admission_recorded=True,
        )

    def _recovery_policy(self, project_id: str) -> R2RecoveryPolicy:
        stored = self._policies.read_policy(project_id)
        if stored is None:
            raise ValueError("authoritative project policy is missing")
        return AuthoritativeExecutionPolicy.from_project_policy(stored).r2_recovery_policy

    def _stale_head_execution(
        self,
        project_id: str,
        portfolio: HypothesisPortfolio,
        action_plan: ActionPlan,
        action_id: str,
        bundle: SandboxExecutionBundle,
        *,
        admission_recorded: bool = False,
    ) -> R2ClosedLoopExecution:
        if not admission_recorded:
            self._sandbox.reject_observation(bundle, reason="SANDBOX_HEAD_STALE_AFTER_IO")
        current_portfolio = self._current_portfolio(project_id, portfolio)
        current_plan = self._current_action_plan(project_id, action_plan)
        return R2ClosedLoopExecution(
            projection=stale_head_projection(action_id, bundle),
            portfolio=current_portfolio,
            action_plan=current_plan,
        )

    async def _run_or_hold(
        self,
        spec: SandboxRunSpec,
        action_id: str,
    ) -> tuple[SandboxExecutionBundle | None, R2ClosedLoopExecution | None]:
        try:
            work = research_work.get()
            check_research_boundary()
            if work is not None:
                work.boundary.reserve(len(canonical_payload(spec)))
                if work.observe_external_effect is not None:
                    work.observe_external_effect(spec.attempt_id, "DISPATCHING")
            bundle = await self._sandbox.run(spec)
            check_research_boundary()
            if work is not None and work.observe_external_effect is not None:
                work.observe_external_effect(spec.attempt_id, "RETURNED")
            return bundle, None
        except SandboxFailure as exc:
            return None, R2ClosedLoopExecution(
                projection=sandbox_failure_projection(exc, action_id)
            )

    def _current_portfolio(
        self,
        project_id: str,
        fallback: HypothesisPortfolio,
    ) -> HypothesisPortfolio:
        return self._research.read_portfolio(project_id, fallback.portfolio_id)

    def _current_action_plan(
        self,
        project_id: str,
        fallback: ActionPlan,
    ) -> ActionPlan:
        return self._research.read_action_plan(project_id, fallback.plan_id)

    def _failure_execution(
        self,
        *,
        project: Project,
        action_plan: ActionPlan,
        action_id: str,
        spec: SandboxRunSpec,
        failure_class: ExecutionFailureClass,
        attempts: tuple[RecoveryAttempt, ...],
        expected_heads: dict[str, str],
        expected_head_set_digest: str,
        retry_budget_exhausted: bool,
        test_completion: ResearchTestCompletion | None = None,
    ) -> R2ClosedLoopExecution:
        if test_completion is not None:
            expected_heads = dict(self._ledger.read_heads(project.project_id))
            expected_head_set_digest = head_set_digest(expected_heads)
        baseline_heads = dict(expected_heads)
        baseline_digest = head_set_digest(baseline_heads)
        repair_basis = repair_basis_text(failure_class, attempts)
        candidate = recovery_candidate(
            self._ids,
            action_plan,
            action_id,
            baseline_digest,
            failure_class,
            repair_basis,
            attempts,
        )
        reconciliation = None
        if failure_class == ExecutionFailureClass.AMBIGUOUS_EXTERNAL:
            reconciliation = reconciliation_record(self._ids, action_id, attempts)
        revised_plan = action_plan.model_copy(update={"recovery_candidate": candidate})
        action_head_key = f"ACTION:{action_plan.plan_id}"
        actor = recovery_actor()
        research = self._research.context(project.project_id, action_plan.object_id, baseline_heads)
        batch = self._research.stage_actions(
            revised_plan, actor=actor, context=research, hypotheses=research.hypotheses
        )
        # Recovery proposes a plan-only branch; no action/portfolio projection is activated.
        staged = batch.staged[0]
        commit = RevisionCommitService(
            self._ledger,
            self._clock,
            self._ids,
            policy_version=spec.policy_digest,
        ).commit(
            RevisionChangeSet(
                changeset_id=self._ids.new("changeset"),
                project_id=project.project_id,
                expected_heads={action_head_key: "0" * 64},
                expected_head_set_digest=expected_head_set_digest,
                staged_revisions=(staged,),
                impact_plan=ImpactPropagationPlan(),
                actor=actor,
                reason=repair_basis,
            )
        )
        baseline_after = head_set_digest(self._ledger.read_heads(project.project_id))
        terminal = terminal_state(failure_class, retry_budget_exhausted)
        return R2ClosedLoopExecution(
            projection=failure_projection(
                terminal,
                test_completion,
                action_id,
                attempts,
                baseline_digest,
                baseline_after,
                commit,
                repair_basis,
                reconciliation,
                retry_budget_exhausted,
            )
        )
