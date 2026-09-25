from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from thoth.application.services.r2_outcome_projection import (
    ProcessOutcomeProjection,
    build_process_outcome,
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
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import (
    ActorKind,
    EntityType,
    RiskTier,
)
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.policy import AuthoritativeExecutionPolicy, R2RecoveryPolicy
from thoth.domain.post_execution_learning import CommittedExecutionBasis
from thoth.domain.project import Project, WorkThread
from thoth.domain.recovery import (
    ExecutionFailureClass,
    ReconciliationRecord,
    RecoveryAttempt,
    RecoveryCandidateRevision,
)
from thoth.domain.research_execution import check_research_boundary, research_work
from thoth.domain.research_request import RevisionRef
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.domain.sandbox import (
    SandboxErrorCode,
    SandboxExecutionState,
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
        by_id = {item.action_id: item for item in action_plan.alternatives}
        action = next(
            (
                by_id[action_id]
                for action_id in action_plan.frontier
                if action_id in by_id and by_id[action_id].risk_tier == RiskTier.R2
            ),
            None,
        )
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
            failure_class = self._failure_class(bundle, recovery_policy)
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
            attempts.append(
                RecoveryAttempt(
                    attempt_id=spec.attempt_id,
                    input_context_digest=cast(str, spec.input_context_digest),
                    result_state=bundle.result.state.value,
                    failure_class=failure_class,
                    failure_detail=bundle.result.failure_detail,
                    retry_ordinal=retry_ordinal,
                )
            )
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
            outcome = outcome.model_copy(
                update={
                    "test_validity_assessment_refs": tuple(
                        item.assessment_id for item in assessments
                    ),
                    "interpretation": "process observation links to scoped test assessments",
                    "limitations": (
                        "official criterion attainment and causal attribution remain NOT_ASSESSED",
                    ),
                }
            )
        actor = ActorRef(
            actor_id="agent:r2-closed-loop-coordinator",
            kind=ActorKind.AGENT,
            role="trusted-r2-closed-loop-coordinator",
        )
        research_batch = self._research.stage_cycle(
            process.portfolio, process.plan, actor=actor, context=research
        )
        staged = (
            research_batch.hypotheses.staged[0],
            research_batch.actions.staged[0],
            self._stage(
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
            projection={
                **(
                    {}
                    if test_completion is None
                    else {"test_lifecycle": test_completion.projection}
                ),
                "terminal_state": "COMPLETED",
                "compiled_spec": cast(JsonValue, spec.model_dump(mode="json")),
                "sandbox_result": cast(JsonValue, bundle.result.model_dump(mode="json")),
                "sandbox_receipt": cast(JsonValue, bundle.receipt.model_dump(mode="json")),
                "observation_refs": list(process.observation_refs),
                "outcome": cast(JsonValue, outcome.model_dump(mode="json")),
                "outcome_validity": cast(JsonValue, process.validity.model_dump(mode="json")),
                "hypothesis_appraisal": cast(JsonValue, process.appraisal.model_dump(mode="json")),
                "next_action_order": list(process.next_order),
                "non_truth_receipt": cast(JsonValue, commit.receipt.model_dump(mode="json")),
                "scientific_truth_state": "NOT_CERTIFIED",
                "recovery": {
                    "attempts": [item.model_dump(mode="json") for item in attempts],
                    "retry_count": retry_ordinal,
                    "baseline_head_before": spec.current_head_set_digest,
                    "baseline_head_after": commit.after_head_set_digest,
                    "candidate_branch_revision_ids": [],
                    "repair_basis": None,
                    "reconciliation": None,
                    "retry_budget_exhausted": False,
                    "failure_receipt": commit.receipt.model_dump(mode="json"),
                },
            },
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
            return None, R2ClosedLoopExecution(
                projection={
                    "terminal_state": "POLICY_BLOCKED" if exc.policy_denial is not None else "HOLD",
                    "reason_code": exc.code.value,
                    "stage": "PRE_IO",
                    "runtime_io_completed": False,
                    "policy_denial": None
                    if exc.policy_denial is None
                    else cast(JsonValue, exc.policy_denial.model_dump(mode="json")),
                    "scientific_truth_state": "NOT_CERTIFIED",
                }
            )
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
        reason = str(error)
        code = (
            reason
            if reason
            and len(reason) <= 160
            and all(
                character.isupper() or character.isdigit() or character == "_"
                for character in reason
            )
            else "RESEARCH_TEST_INPUT_INVALID"
        )
        return R2ClosedLoopExecution(
            projection={
                "terminal_state": "HOLD",
                "reason_code": code,
                "runtime_io_completed": bundle is not None,
                "scientific_truth_state": "NOT_CERTIFIED",
                "sandbox_receipt": None
                if bundle is None
                else cast(JsonValue, bundle.receipt.model_dump(mode="json")),
            }
        )

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
                projection={
                    "terminal_state": "HOLD",
                    "reason_code": "SANDBOX_TEMPLATE_REQUIRED",
                    "action_id": action.action_id,
                    "scientific_truth_state": "NOT_CERTIFIED",
                }
            )

        except SandboxFailure as exc:
            return None, R2ClosedLoopExecution(
                projection={
                    "terminal_state": (
                        "POLICY_BLOCKED" if exc.policy_denial is not None else "HOLD"
                    ),
                    "action_id": action.action_id,
                    "policy_denial": (
                        None
                        if exc.policy_denial is None
                        else cast(JsonValue, exc.policy_denial.model_dump(mode="json"))
                    ),
                    "scientific_truth_state": "NOT_CERTIFIED",
                }
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
                projection={
                    "terminal_state": "POLICY_BLOCKED",
                    "reason_code": "SANDBOX_ADMISSION_POLICY_CHANGED",
                    "stage": "POST_IO_ADMISSION",
                    "runtime_io_completed": True,
                    "action_id": action_id,
                    "scientific_truth_state": "NOT_CERTIFIED",
                    "policy_denial": cast(JsonValue, failure.policy_denial.model_dump(mode="json")),
                    "sandbox_result": cast(JsonValue, bundle.result.model_dump(mode="json")),
                }
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
            projection={
                "terminal_state": "HOLD",
                "reason_code": "SANDBOX_HEAD_STALE_AFTER_IO",
                "action_id": action_id,
                "sandbox_result": cast(JsonValue, bundle.result.model_dump(mode="json")),
                "sandbox_receipt": cast(JsonValue, bundle.receipt.model_dump(mode="json")),
                "scientific_truth_state": "NOT_CERTIFIED",
            },
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
                projection={
                    "terminal_state": (
                        "POLICY_BLOCKED" if exc.policy_denial is not None else "HOLD"
                    ),
                    "action_id": action_id,
                    "policy_denial": (
                        None
                        if exc.policy_denial is None
                        else cast(JsonValue, exc.policy_denial.model_dump(mode="json"))
                    ),
                    "scientific_truth_state": "NOT_CERTIFIED",
                }
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

    def _failure_class(
        self,
        bundle: SandboxExecutionBundle,
        policy: R2RecoveryPolicy,
    ) -> ExecutionFailureClass | None:
        if bundle.result.state == SandboxExecutionState.SUCCEEDED:
            return None
        return self._classify_failure(
            bundle.result.state.value,
            bundle.result.failure_detail,
            policy.transient_markers,
            policy.semantic_markers,
            policy.code_markers,
            policy.ambiguous_markers,
        )

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
        repair_basis = (
            f"create a new candidate after {failure_class.value}: "
            f"{attempts[-1].failure_detail or 'unspecified failure'}"
        )
        original_specification = next(
            item.specification for item in action_plan.alternatives if item.action_id == action_id
        )
        candidate_draft: dict[str, object] = {
            "candidate_id": self._ids.new("recovery-candidate"),
            "action_id": action_id,
            "plan_id": action_plan.plan_id,
            "baseline_head_digest": baseline_digest,
            "failure_class": failure_class,
            "revised_specification": (
                f"{original_specification} [candidate revision: {failure_class.value}]"
            ),
            "repair_basis": repair_basis,
            "attempt_ids": tuple(item.attempt_id for item in attempts),
        }
        candidate = RecoveryCandidateRevision.model_validate(
            {
                **candidate_draft,
                "candidate_digest": domain_digest(
                    "R2_RECOVERY_CANDIDATE",
                    "1.0.0",
                    canonical_payload(candidate_draft),
                ),
            }
        )
        reconciliation = None
        if failure_class == ExecutionFailureClass.AMBIGUOUS_EXTERNAL:
            reconciliation_draft: dict[str, object] = {
                "reconciliation_id": self._ids.new("reconciliation"),
                "action_id": action_id,
                "attempt_id": attempts[-1].attempt_id,
                "state": "REQUIRED",
                "automatic_retry_allowed": False,
                "reason": attempts[-1].failure_detail or "ambiguous external result",
            }
            reconciliation = ReconciliationRecord.model_validate(
                {
                    **reconciliation_draft,
                    "reconciliation_digest": domain_digest(
                        "R2_RECONCILIATION",
                        "1.0.0",
                        canonical_payload(reconciliation_draft),
                    ),
                }
            )
        revised_plan = action_plan.model_copy(update={"recovery_candidate": candidate})
        action_head_key = f"ACTION:{action_plan.plan_id}"
        actor = ActorRef(
            actor_id="agent:r2-recovery-coordinator",
            kind=ActorKind.AGENT,
            role="bounded-r2-recovery-coordinator",
        )
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
        terminal = (
            "RECONCILIATION_REQUIRED"
            if failure_class == ExecutionFailureClass.AMBIGUOUS_EXTERNAL
            else "RETRY_BUDGET_EXHAUSTED"
            if retry_budget_exhausted
            else "CANDIDATE_REVISION_CREATED"
            if failure_class in {ExecutionFailureClass.SEMANTIC, ExecutionFailureClass.CODE}
            else "FAILED"
        )
        return R2ClosedLoopExecution(
            projection={
                "terminal_state": terminal,
                **(
                    {}
                    if test_completion is None
                    else {"test_lifecycle": test_completion.projection}
                ),
                "action_id": action_id,
                "scientific_truth_state": "NOT_CERTIFIED",
                "recovery": {
                    "attempts": [item.model_dump(mode="json") for item in attempts],
                    "retry_count": max(0, len(attempts) - 1),
                    "baseline_head_before": baseline_digest,
                    "baseline_head_after": baseline_after,
                    "candidate_branch_revision_ids": list(commit.branch_revision_ids),
                    "repair_basis": repair_basis,
                    "reconciliation": (
                        None if reconciliation is None else reconciliation.model_dump(mode="json")
                    ),
                    "retry_budget_exhausted": retry_budget_exhausted,
                    "failure_receipt": commit.receipt.model_dump(mode="json"),
                },
            }
        )

    @staticmethod
    def _classify_failure(
        state: str,
        detail: str | None,
        transient_markers: tuple[str, ...],
        semantic_markers: tuple[str, ...],
        code_markers: tuple[str, ...],
        ambiguous_markers: tuple[str, ...],
    ) -> ExecutionFailureClass:
        corpus = f"{state} {detail or ''}".upper()
        if any(marker.upper() in corpus for marker in ambiguous_markers):
            return ExecutionFailureClass.AMBIGUOUS_EXTERNAL
        if any(marker.upper() in corpus for marker in semantic_markers):
            return ExecutionFailureClass.SEMANTIC
        if any(marker.upper() in corpus for marker in code_markers):
            return ExecutionFailureClass.CODE
        if any(marker.upper() in corpus for marker in transient_markers):
            return ExecutionFailureClass.TRANSIENT_INFRA
        return ExecutionFailureClass.PERMANENT_INFRA

    def _stage(
        self,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        content: dict[str, object],
        heads: dict[str, str],
    ) -> StagedRevision:
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            schema_version="1.0.0",
            content=content,
            content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        parent = heads.get(f"{entity_type.value}:{entity_id}")
        revision_draft: dict[str, object] = {
            "revision_id": self._ids.new("revision"),
            "project_id": project_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "snapshot_id": snapshot.snapshot_id,
            "parent_revision_digests": () if parent is None else (parent,),
            "actor": ActorRef(
                actor_id="agent:r2-closed-loop-coordinator",
                kind=ActorKind.AGENT,
                role="trusted-r2-closed-loop-coordinator",
            ),
            "reason": "R2 sandbox observation integration",
            "evidence_refs": (),
            "affected_refs": (),
            "created_at": self._clock.now(),
            "schema_version": "1.0.0",
        }
        revision = SemanticRevision.model_validate(
            {
                **revision_draft,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION", "1.0.0", canonical_payload(revision_draft)
                ),
            }
        )
        return StagedRevision(snapshot=snapshot, revision=revision)
