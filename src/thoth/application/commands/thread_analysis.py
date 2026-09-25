from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic import Field, JsonValue, ValidationError

from thoth.application.reducers import SufficiencySignals
from thoth.application.services import (
    AcquisitionCoordinator,
    BaselineService,
    CriterionContractService,
    CriticalCounterSearchCoordinator,
    FullProjectMemoryService,
    R2ClosedLoopCoordinator,
    ReceiptDagService,
    RecursiveImprovementCoordinator,
    RevisionCommitService,
)
from thoth.application.services.behavior_context import (
    select_evidence_for_behavior as select_evidence_context,
)
from thoth.application.services.criterion_projection import (
    criterion_projection as _criterion_projection,
)
from thoth.application.services.post_execution_memory import PostExecutionMemory
from thoth.application.services.r2_closed_loop import R2ClosedLoopExecution
from thoth.application.services.research_basis_capture import (
    cycle_result_payload,
    render_analysis_memory,
)
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.revision_service import CommitDisposition
from thoth.application.workflows import ThreadCycleCommand, ThreadCycleService
from thoth.application.workflows.thread_cycle import ThreadCycleBranchResult, ThreadCycleResult
from thoth.domain.action import ActionCompilationPolicy, ActionPlan
from thoth.domain.actor import ActorRef
from thoth.domain.auth import authenticated_data_scope_allows, current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.baseline import MultiBaselineProjection
from thoth.domain.canonical import head_set_digest
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.enums import (
    ActorKind,
    ProjectLifecycle,
    RiskTier,
    ThreadExecutionState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.improvement import RecursiveImprovementResult
from thoth.domain.memory_preparation import FullMemoryPromotionResult
from thoth.domain.post_execution_learning import PostExecutionLearningResult
from thoth.domain.project import Project, WorkThread
from thoth.domain.receipt_dag import ReceiptNodeKind
from thoth.domain.reference import ReferenceAnswerRequest, ReferenceRequest
from thoth.domain.research_execution import ResearchWork, check_research_boundary, research_work
from thoth.domain.thread_runtime import ThreadInputRecord
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.criterion_profile import CriterionProfileRouterPort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import MemoryStorePort
from thoth.ports.model import ModelExecutionHold, ModelPort, ModelResolutionError, ModelResolverPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadStorePort
from thoth.ports.thread_runtime import ThreadRuntimeStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ThreadInputRequest(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str = Field(min_length=1, max_length=160)
    provider: str = "default"
    model: str | None = Field(default=None, max_length=160)
    expected_head_overrides: dict[str, str] = Field(default_factory=dict)
    instruction: str | None = Field(default=None, max_length=20_000)
    reference_request: ReferenceRequest | None = None
    reference_answer: ReferenceAnswerRequest | None = None


@dataclass(frozen=True)
class _CycleExecution:
    result: ThreadCycleResult
    selected_evidence: tuple[EvidenceSpan, ...]
    acquisition_projection: dict[str, JsonValue] | None
    critical_counter_projection: dict[str, JsonValue] | None
    r2_closed_loop_projection: dict[str, JsonValue] | None
    full_memory: FullMemoryPromotionResult
    learning: PostExecutionLearningResult | None
    improvement: RecursiveImprovementResult | None
    multi_baseline: MultiBaselineProjection


class ThreadAnalysisCommandHandlers:
    def __init__(
        self,
        *,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        memory: MemoryStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        dependencies: DependencyGraphPort,
        governance: GovernanceStorePort,
        thread_runtime: ThreadRuntimeStorePort,
        criteria: CriterionContractStorePort,
        criterion_service: CriterionContractService,
        models: ModelResolverPort,
        acquisition: AcquisitionCoordinator,
        critical_counter_search: CriticalCounterSearchCoordinator,
        r2_closed_loop: R2ClosedLoopCoordinator,
        receipt_dag: ReceiptDagService,
        full_memory: FullProjectMemoryService,
        unit_of_work: AtomicUnitOfWorkPort,
        recursive_improvement: RecursiveImprovementCoordinator,
        criterion_profiles: CriterionProfileRouterPort,
        baselines: BaselineService,
        research: ResearchIdentityService,
        post_execution_memory: PostExecutionMemory,
    ) -> None:
        self._projects = projects
        self._threads = threads
        self._artifacts = artifacts
        self._ledger = ledger
        self._memory = memory
        self._clock = clock
        self._ids = ids
        self._dependencies = dependencies
        self._governance = governance
        self._thread_runtime = thread_runtime
        self._criteria = criteria
        self._criterion_service = criterion_service
        self._models = models
        self._acquisition = acquisition
        self._critical_counter_search = critical_counter_search
        self._r2_closed_loop = r2_closed_loop
        self._receipt_dag = receipt_dag
        self._full_memory = full_memory
        self._unit_of_work = unit_of_work
        self._recursive_improvement = recursive_improvement
        self._criterion_profiles = criterion_profiles
        self._baselines = baselines
        self._research = research
        self._post_execution_memory = post_execution_memory

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        del method
        project_id = value.get("project_id")
        thread_id = value.get("thread_id")
        if not isinstance(project_id, str) or not isinstance(thread_id, str):
            return
        thread = self._threads.read(thread_id)
        if (
            thread is not None
            and thread.project_id == project_id
            and not authenticated_data_scope_allows(thread.scope)
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "stored Thread workstream is outside the authenticated data scope",
                data={"reason_code": "AUTH_DATA_SCOPE_DENIED", "pre_io": True},
            )

    async def analyze(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        work = research_work.get()
        check_research_boundary()
        request = ThreadInputRequest.model_validate(value)
        project = self._projects.read(request.project_id)
        thread = self._threads.read(request.thread_id)
        if project is None:
            raise RpcApplicationError(RpcErrorCode.PROJECT_NOT_FOUND, "project was not found")
        if thread is None or thread.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.THREAD_NOT_FOUND,
                "thread was not found in this project",
            )
        if not authenticated_data_scope_allows(thread.scope):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "stored Thread workstream is outside the authenticated data scope",
                data={"reason_code": "AUTH_DATA_SCOPE_DENIED", "pre_io": True},
            )
        try:
            model = self._models.resolve(provider=request.provider, model=request.model)
        except ModelResolutionError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        if thread.execution_state == ThreadExecutionState.RUNNING and not (
            work is not None and work.boundary.owns_attempt()
        ):
            if work is not None:
                return {
                    "queued": True,
                    "queue_mode": "AFTER_CURRENT",
                    "reason_code": "THREAD_BUSY_V2",
                }
            if not request.instruction:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "running thread requires an instruction to queue",
                )
            queued = self._enqueue(thread, request.instruction, kind="INPUT")
            return {
                "queued": True,
                "queue_mode": "AFTER_CURRENT",
                "input": queued.model_dump(mode="json"),
            }
        active_artifact_ids = {
            binding.artifact_id
            for binding in self._governance.list_source_bindings(request.project_id)
            if binding.state == "ACTIVE"
        }
        evidence = tuple(
            span
            for span in self._artifacts.list_evidence(request.project_id)
            if span.artifact_id in active_artifact_ids
        )
        if not evidence and work is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "thread/input requires at least one connected evidence span",
            )
        if request.instruction and work is None:
            self._enqueue(thread, request.instruction, kind="INPUT")
        pending_inputs = self._thread_runtime.list_pending_inputs(
            request.project_id, request.thread_id
        )
        analysis_problem = thread.problem if work is None else work.effective_question
        if pending_inputs and work is None:
            analysis_problem += "\n\nAuthorized queued context:\n" + "\n".join(
                f"[{item.kind}] {item.text}" for item in pending_inputs
            )
        try:
            full_memory_context = self._full_memory.build_context(
                project_id=request.project_id,
                thread_id=thread.thread_id,
                query=analysis_problem,
                target_use="WORKING_CONTEXT",
                scope=thread.scope,
                cutoff_at=project.cutoff_at,
            )
        except ValidationError:
            full_memory_context = None
        analysis_problem += render_analysis_memory(
            work, () if full_memory_context is None else full_memory_context.included
        )
        running = thread.model_copy(
            update={
                "execution_state": ThreadExecutionState.RUNNING,
                "revision": thread.revision + 1,
                "updated_at": self._clock.now(),
            }
        )
        retrieval_evidence = evidence if work is None or not work.evidence else work.evidence
        selected_evidence = select_evidence_context(
            problem=analysis_problem,
            evidence=retrieval_evidence,
        ).selected
        if (
            work is not None
            and work.evidence
            and {s.span_id for s in selected_evidence} != {s.span_id for s in work.evidence}
        ):
            raise ModelExecutionHold("REVIEWED_CONTEXT_OMITTED_BY_ACTIVE_POLICY")
        criterion_contracts = self._criteria.list_contracts(request.project_id)
        profile_decision = self._criterion_profiles.route(
            project_id=request.project_id,
            evidence=selected_evidence,
        )
        if not criterion_contracts and not profile_decision.profile_decision_required:
            try:
                compiled, _commit = self._criterion_service.compile(
                    project_id=request.project_id,
                    thread_id=request.thread_id,
                    source_span_ids=tuple(span.span_id for span in selected_evidence),
                    goal_requirement_refs=(),
                    profile_refs=profile_decision.selected_profile_refs,
                )
                criterion_contracts = (compiled,)
            except (ValueError, ValidationError):
                criterion_contracts = ()
        criteria = tuple(_criterion_projection(contract) for contract in criterion_contracts)
        if not self._threads.update(running, expected_revision=thread.revision):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "thread revision changed before analysis"
            )
        try:
            outcome = await self._execute_cycle(
                request=request,
                project=project,
                thread=thread,
                model=model,
                work=work,
                analysis_problem=analysis_problem,
                criteria=criteria,
                selected_evidence=selected_evidence,
                pending_inputs=pending_inputs,
            )
            if isinstance(outcome, dict):
                return outcome
        except (ModelExecutionHold, ValueError) as exc:
            if work is not None and isinstance(exc, ModelExecutionHold):
                raise
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                f"thread analysis held: {str(exc)[:1200]}",
            ) from exc
        finally:
            self._finish_execution(request, running, work)
        payload = cycle_result_payload(outcome.result, work)
        payload["selected_evidence_refs"] = [
            span.span_id for span in outcome.selected_evidence
        ]
        payload["selected_evidence_count"] = len(outcome.selected_evidence)
        payload["autonomous_acquisition"] = outcome.acquisition_projection
        payload["critical_counter_search"] = outcome.critical_counter_projection
        payload["r2_closed_loop"] = outcome.r2_closed_loop_projection
        payload["full_project_memory"] = outcome.full_memory.model_dump(mode="json")
        payload["full_project_memory_context"] = (
            None if full_memory_context is None else full_memory_context.model_dump(mode="json")
        )
        payload["post_execution_learning"] = (
            None if outcome.learning is None else outcome.learning.model_dump(mode="json")
        )
        payload["recursive_improvement"] = (
            None if outcome.improvement is None else outcome.improvement.model_dump(mode="json")
        )
        payload["improvement_observation"] = self._recursive_improvement.observation(
            request.project_id,
            thread.thread_id,
            None
            if outcome.r2_closed_loop_projection is None
            else dict(outcome.r2_closed_loop_projection),
            outcome.improvement,
        ).model_dump(mode="json")
        payload["criterion_profile_decision"] = profile_decision.model_dump(mode="json")
        payload["multi_baseline"] = outcome.multi_baseline.model_dump(mode="json")
        return {str(key): child for key, child in payload.items()}

    async def _execute_cycle(
        self,
        *,
        request: ThreadInputRequest,
        project: Project,
        thread: WorkThread,
        model: ModelPort,
        work: ResearchWork | None,
        analysis_problem: str,
        criteria: tuple[CriterionCandidate, ...],
        selected_evidence: tuple[EvidenceSpan, ...],
        pending_inputs: tuple[ThreadInputRecord, ...],
    ) -> _CycleExecution | dict[str, JsonValue]:
        acquisition_projection: dict[str, JsonValue] | None = None
        critical_counter_projection: dict[str, JsonValue] | None = None
        r2_closed_loop_projection: dict[str, JsonValue] | None = None
        learning: PostExecutionLearningResult | None = None
        initiator = current_authenticated_actor()
        cycle = self._create_cycle(model, project)
        cycle_command = ThreadCycleCommand(
            case_id=f"case:{thread.thread_id}",
            project_id=request.project_id,
            thread_id=thread.thread_id,
            object_id=thread.current_object_ids[0],
            problem=analysis_problem,
            cutoff_at=project.cutoff_at,
            criteria=criteria,
            evidence=selected_evidence,
            sufficiency_signals=SufficiencySignals(),
            action_policy=_default_action_policy(),
            actor=ActorRef(
                actor_id=f"agent:{request.provider}",
                kind=ActorKind.AGENT,
                role="thread-cycle-runner",
                project_id=request.project_id,
                session_id=None if initiator is None else initiator.session_id,
                role_assignment_ref=(
                    None if initiator is None else initiator.role_assignment_id
                ),
            ),
            policy_version=project.policy_binding_ref,
            model_policy_ref=f"model-policy:{request.provider}-v1",
            expected_head_overrides=request.expected_head_overrides,
            memory_scope=thread.scope,
        )
        result = await cycle.execute(cycle_command)
        if result.commit.disposition == CommitDisposition.BRANCH:
            return ThreadCycleBranchResult(
                project_id=request.project_id,
                thread_id=thread.thread_id,
                commit=result.commit,
            ).model_dump(mode="json")
        check_research_boundary()
        acquisition = (
            None
            if work is not None
            else await self._acquisition.execute_if_required(
                project=project,
                thread=thread,
                assessment=result.assessment,
                evidence=selected_evidence,
            )
        )
        if acquisition is not None:
            acquisition_projection = acquisition.projection
            if acquisition.projection.get("reanalysis_performed") is True:
                refreshed_artifact_ids = {
                    binding.artifact_id
                    for binding in self._governance.list_source_bindings(request.project_id)
                    if binding.state == "ACTIVE"
                }
                refreshed_evidence = tuple(
                    span
                    for span in self._artifacts.list_evidence(request.project_id)
                    if span.artifact_id in refreshed_artifact_ids
                )
                selected_evidence = select_evidence_context(
                    problem=thread.problem,
                    evidence=refreshed_evidence,
                ).selected
                result = await cycle.execute(
                    replace(
                        cycle_command, evidence=selected_evidence, expected_head_overrides={}
                    )
                )
                if result.commit.disposition == CommitDisposition.BRANCH:
                    return ThreadCycleBranchResult(
                        project_id=request.project_id,
                        thread_id=thread.thread_id,
                        commit=result.commit,
                        prior_acquisition_completed=True,
                    ).model_dump(mode="json")
        check_research_boundary()
        critical_counter = await self._critical_counter_search.execute_if_required(
            project=project,
            thread=thread,
            portfolio=result.portfolio,
        )
        if critical_counter is not None:
            critical_counter_projection = critical_counter.projection
            final_portfolio = critical_counter.projection.get("portfolio")
            if isinstance(final_portfolio, dict):
                result = result.model_copy(
                    update={"portfolio": HypothesisPortfolio.model_validate(final_portfolio)}
                )
        check_research_boundary()
        r2_execution = await self._execute_r2(
            project=project,
            thread=thread,
            portfolio=result.portfolio,
            action_plan=result.action_plan,
        )
        if r2_execution is not None:
            r2_closed_loop_projection = r2_execution.projection
            updates: dict[str, object] = {}
            if r2_execution.portfolio is not None:
                updates["portfolio"] = r2_execution.portfolio
            if r2_execution.action_plan is not None:
                updates["action_plan"] = r2_execution.action_plan
            if updates:
                result = result.model_copy(update=updates)
        full_memory = result.full_memory
        if full_memory is None:
            raise ValueError("full project memory lifecycle was not available")
        if (
            work is not None
            and r2_execution is not None
            and r2_execution.committed_basis is not None
        ):
            learning = await self._post_execution_memory.learn(
                project, thread, work, r2_execution.committed_basis
            )
            if learning.promotion is not None:
                full_memory = learning.promotion
                result = result.model_copy(update={"full_memory": full_memory})
        improvement = await self._recursive_improvement.observe(
            project_id=request.project_id,
            thread_id=thread.thread_id,
            r2_projection=(
                None
                if r2_closed_loop_projection is None
                else {str(key): child for key, child in r2_closed_loop_projection.items()}
            ),
        )
        multi_baseline = self._baselines.refresh(
            project_id=request.project_id,
            thread_id=thread.thread_id,
            purpose=f"Thread milestone preview: {thread.problem}",
        )
        self._record_cycle_lineage(
            request, result, selected_evidence, r2_closed_loop_projection, work
        )
        self._thread_runtime.mark_inputs_consumed(
            tuple(item.input_id for item in pending_inputs)
        )
        return _CycleExecution(
            result=result,
            selected_evidence=selected_evidence,
            acquisition_projection=acquisition_projection,
            critical_counter_projection=critical_counter_projection,
            r2_closed_loop_projection=r2_closed_loop_projection,
            full_memory=full_memory,
            learning=learning,
            improvement=improvement,
            multi_baseline=multi_baseline,
        )

    def _finish_execution(
        self, request: ThreadInputRequest, running: WorkThread, work: ResearchWork | None
    ) -> None:
        heads = self._ledger.read_heads(request.project_id)
        current_thread = self._threads.read(request.thread_id) or running
        owned = work is None or work.boundary.owns_attempt()
        execution_state = current_thread.execution_state
        if execution_state == ThreadExecutionState.PAUSE_PENDING:
            execution_state = ThreadExecutionState.PAUSED
        elif execution_state == ThreadExecutionState.RUNNING:
            execution_state = ThreadExecutionState.IDLE
        if owned:
            self._threads.update(
                current_thread.model_copy(
                    update={
                        "execution_state": execution_state,
                        "working_head_digest": head_set_digest(heads),
                        "revision": current_thread.revision + 1,
                        "updated_at": self._clock.now(),
                    }
                ),
                expected_revision=current_thread.revision,
            )

    def _record_cycle_lineage(
        self,
        request: ThreadInputRequest,
        result: ThreadCycleResult,
        selected_evidence: tuple[EvidenceSpan, ...],
        r2_closed_loop_projection: dict[str, JsonValue] | None,
        work: ResearchWork | None,
    ) -> None:
        full_memory = result.full_memory
        assert full_memory is not None
        dag_values: dict[ReceiptNodeKind, tuple[str, ...]] = {
            ReceiptNodeKind.EVIDENCE: tuple(span.span_id for span in selected_evidence),
            ReceiptNodeKind.MODEL: result.model_ids,
            ReceiptNodeKind.ACTION: (result.action_plan.plan_id,),
            ReceiptNodeKind.REVISION: (result.commit.receipt.receipt_id,),
            ReceiptNodeKind.MEMORY: tuple(
                item.memory_revision_id
                for item in (
                    *full_memory.committed,
                    *full_memory.revised,
                    *full_memory.held,
                    *full_memory.quarantined,
                )
            ),
        }
        if work is not None:
            dag_values[ReceiptNodeKind.REVISION] = (
                *dag_values[ReceiptNodeKind.REVISION],
                work.request_ref.revision_digest,
                *(ref.revision_digest for ref in work.record_refs),
            )
        if result.outcome is not None:
            dag_values[ReceiptNodeKind.OUTCOME] = (result.outcome.outcome_id,)
        if r2_closed_loop_projection is not None:
            compiled = r2_closed_loop_projection.get("compiled_spec")
            sandbox_receipt = r2_closed_loop_projection.get("sandbox_receipt")
            outcome_value = r2_closed_loop_projection.get("outcome")
            if isinstance(compiled, dict):
                dag_values[ReceiptNodeKind.EXECUTION] = (str(compiled.get("attempt_id")),)
            if isinstance(sandbox_receipt, dict):
                dag_values[ReceiptNodeKind.SANDBOX] = (str(sandbox_receipt.get("receipt_digest")),)
            if isinstance(outcome_value, dict):
                dag_values[ReceiptNodeKind.OUTCOME] = (str(outcome_value.get("outcome_id")),)
        self._receipt_dag.record_cycle(request.project_id, dag_values, result.commit.receipt)

    def _create_cycle(self, model: ModelPort, project: Project) -> ThreadCycleService:
        return ThreadCycleService(
            research=self._research,
            ledger=self._ledger,
            memory=self._memory,
            model=model,
            commits=RevisionCommitService(
                self._ledger,
                self._clock,
                self._ids,
                policy_version=project.policy_binding_ref,
            ),
            clock=self._clock,
            ids=self._ids,
            dependencies=self._dependencies,
            unit_of_work=self._unit_of_work,
            full_memory=self._full_memory,
        )

    async def _execute_r2(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
        action_plan: ActionPlan,
    ) -> R2ClosedLoopExecution | None:
        if not any(
            item.risk_tier == RiskTier.R2 and item.action_id in action_plan.frontier
            for item in action_plan.alternatives
        ):
            return None
        current = self._projects.read(project.project_id)
        if (
            current is None
            or current.cutoff_at != project.cutoff_at
            or current.policy_binding_ref != project.policy_binding_ref
            or current.overlay != project.overlay
            or current.lifecycle in {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        ):
            return R2ClosedLoopExecution(
                projection={
                    "terminal_state": "HOLD",
                    "reason_code": "R2_PROJECT_CONTEXT_CHANGED",
                    "scientific_truth_state": "NOT_CERTIFIED",
                    "pre_io": True,
                }
            )
        authenticated = current_authenticated_actor()
        if authenticated is not None:
            role = next(
                (
                    item
                    for item in self._governance.list_roles(project.project_id)
                    if item.role_assignment_id == authenticated.role_assignment_id
                ),
                None,
            )
            if (
                role is None
                or role.state != "ACTIVE"
                or role.actor_id != authenticated.actor_id
                or role.role != authenticated.role
                or role.scope not in authenticated.data_scopes
            ):
                return R2ClosedLoopExecution(
                    projection={
                        "terminal_state": "AUTHORITY_REQUIRED",
                        "reason_code": "R2_AUTHORITY_CHANGED",
                        "scientific_truth_state": "NOT_CERTIFIED",
                        "pre_io": True,
                    }
                )
        return await self._r2_closed_loop.execute_if_required(
            project=current,
            thread=thread,
            portfolio=portfolio,
            action_plan=action_plan,
        )

    def _enqueue(self, thread: WorkThread, text: str, *, kind: str) -> ThreadInputRecord:
        record = ThreadInputRecord(
            input_id=self._ids.new("thread-input"),
            project_id=thread.project_id,
            thread_id=thread.thread_id,
            kind=kind,
            text=text,
            ordinal=self._thread_runtime.next_input_ordinal(thread.thread_id),
            created_at=self._clock.now(),
        )
        self._thread_runtime.enqueue_input(record)
        return record


def _default_action_policy() -> ActionCompilationPolicy:
    return ActionCompilationPolicy(
        minimum_tier_by_family={
            "EVIDENCE_REQUEST": RiskTier.R0,
            "READ_ONLY_ANALYSIS": RiskTier.R0,
            "SANDBOX_REPLAY": RiskTier.R2,
            "CONFIGURATION_CHANGE": RiskTier.R3,
            "CONTROLLED_RERUN": RiskTier.R3,
            "OFFICIAL_CRITERION_CHANGE": RiskTier.R4,
        },
        approver_role_by_family={
            "EVIDENCE_REQUEST": "project-owner",
            "READ_ONLY_ANALYSIS": "project-owner",
            "SANDBOX_REPLAY": "sandbox-owner",
            "CONFIGURATION_CHANGE": "configuration-owner",
            "CONTROLLED_RERUN": "test-owner",
            "OFFICIAL_CRITERION_CHANGE": "criterion-owner",
        },
        unknown_family_tier=RiskTier.R3,
    )
