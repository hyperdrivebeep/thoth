"""One selected R2 execution connects sealed predictions with producer-owned assessments."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from pydantic import JsonValue

from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.r2_sandbox_compiler import R2SandboxSpecCompiler
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.revision_service import CommitResult
from thoth.application.services.sandbox_service import SandboxExecutionBundle, SandboxService
from thoth.application.services.test_validity_service import ResearchTestRun, TestValidityService
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, head_set_digest
from thoth.domain.enums import ActorKind, RiskTier
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import HypothesisRecord, PredictionRecord
from thoth.domain.project import Project
from thoth.domain.sandbox import SandboxRunSpec
from thoth.domain.test_validity import TestValidityAssessment
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort


@dataclass(frozen=True)
class PreparedResearchExecution:
    plan: ActionPlanRecord
    execution: PlanExecutionRecord
    attempt: StepExecutionAttemptRecord
    predictions: tuple[PredictionRecord, ...]
    portfolio: HypothesisPortfolio
    action_plan: ActionPlan
    spec: SandboxRunSpec


@dataclass(frozen=True)
class ResearchTestCompletion:
    commit: CommitResult
    projection: dict[str, JsonValue]


class R2TestLifecycle:
    def __init__(
        self,
        *,
        hypotheses: HypothesisStorePort,
        hypothesis_service: HypothesisService,
        lifecycle: HypothesisTestLifecycle,
        validity: TestValidityService,
        execution_service: ExecutionService,
        executions: ExecutionStorePort,
        research: ResearchIdentityService,
        compiler: R2SandboxSpecCompiler,
        sandbox: SandboxService,
        ledger: LedgerPort,
    ) -> None:
        self._hypotheses = hypotheses
        self._hypothesis_service = hypothesis_service
        self._lifecycle = lifecycle
        self._validity = validity
        self._execution = execution_service
        self._executions = executions
        self._research = research
        self._compiler = compiler
        self._sandbox = sandbox
        self._ledger = ledger

    def prepare(
        self,
        project: Project,
        portfolio: HypothesisPortfolio,
        action: ActionCandidate,
        spec: SandboxRunSpec,
    ) -> PreparedResearchExecution | None:
        heads = dict(self._ledger.read_heads(project.project_id))
        context = self._research.context(project.project_id, portfolio.object_id, heads)
        targets = tuple(
            item for item in context.hypotheses if item.hypothesis_id in action.hypothesis_ids
        )
        proposals = tuple(
            item.generation_details.prediction_proposal if item.generation_details else None
            for item in targets
        )
        self._reject_pending_result(project.project_id, action, spec)
        if not any(proposals):
            return None
        if action.risk_tier != RiskTier.R2 or action.primary_purpose not in {
            "HYPOTHESIS_DISCRIMINATION",
            "EXPERIMENT_TEST",
            "ANALYSIS_COMPUTATION",
            "SIMULATION",
        }:
            raise ValueError("RESEARCH_TEST_ACTION_PURPOSE_REQUIRED")
        if len(targets) != len(set(action.hypothesis_ids)) or any(
            proposal is None for proposal in proposals
        ):
            raise ValueError("RESEARCH_TEST_PREDICTION_PROPOSAL_MISSING")
        self._sandbox.preflight(spec)
        bases = tuple(
            self._lifecycle.prepare_prediction(
                hypothesis,
                knowledge_cutoff=project.cutoff_at,
                measurement_contract_ref=proposal.measurement_contract_ref,
                conditions=proposal.conditions,
                expected_outcome=proposal.expected_outcome.model_dump(mode="python"),
            )
            for hypothesis, proposal in zip(targets, proposals, strict=True)
            if proposal is not None
        )
        input_artifacts = {item.artifact_id for item in spec.input_snapshots}
        if any(basis.contract_ref not in input_artifacts for basis in bases):
            raise ValueError("MEASUREMENT_CONTRACT_NOT_IN_EXECUTION_INPUTS")
        predictions: list[PredictionRecord] = []
        actor = ActorRef(
            actor_id="agent:research-test-coordinator",
            kind=ActorKind.AGENT,
            role="trusted-research-test-coordinator",
        )
        with self._ledger.transaction():
            if heads != dict(self._ledger.read_heads(project.project_id)):
                raise ValueError("RESEARCH_TEST_PREPARATION_BASIS_CHANGED")
            for hypothesis, proposal, basis in zip(targets, proposals, bases, strict=True):
                assert proposal is not None
                prediction, _revised, _commit = self._hypothesis_service.bind_prediction(
                    hypothesis,
                    knowledge_cutoff=project.cutoff_at,
                    prespecification_state=proposal.prespecification_state,
                    conditions=proposal.conditions,
                    measurement_contract_ref=proposal.measurement_contract_ref,
                    assumption_refs=hypothesis.assumption_refs,
                    expected_outcome=proposal.expected_outcome.model_dump(mode="python"),
                    discrimination_map={
                        "comparison_hypotheses": action.hypothesis_ids,
                        "semantic_exclusivity": "NOT_ASSERTED",
                    },
                    prepared_basis=basis,
                )
                predictions.append(prediction)
            context = self._research.context(
                project.project_id,
                portfolio.object_id,
                dict(self._ledger.read_heads(project.project_id)),
            )
            plan = self._research.bind_execution_plan(
                context, action, spec, tuple(item.prediction_id for item in predictions), actor
            )
            selected = tuple(
                str(step["step_id"])
                for step in plan.steps
                if step.get("action_id") == action.action_id
            )
            if len(selected) != 1:
                raise ValueError("RESEARCH_TEST_EXECUTION_SELECTION_AMBIGUOUS")
            execution, attempts, _commit = self._execution.start(
                project_id=project.project_id,
                plan=plan,
                execution_profile_ref="research-test:1.0.0",
                expected_working_head_digest=head_set_digest(
                    self._ledger.read_heads(project.project_id)
                ),
                selected_step_ids=selected,
            )
            if len(attempts) != 1 or attempts[0].step_id != selected[0]:
                raise ValueError("RESEARCH_TEST_EXECUTION_SELECTION_VIOLATION")
            refreshed = self._research.context(
                project.project_id,
                portfolio.object_id,
                dict(self._ledger.read_heads(project.project_id)),
            )
            if refreshed.portfolio_view is None or refreshed.action_plan_view is None:
                raise ValueError("RESEARCH_TEST_VIEW_UNAVAILABLE")
            bound_spec = self._compiler.compile(
                project=project, plan=refreshed.action_plan_view, action=action
            )
            bound_spec = bound_spec.model_copy(update={"attempt_id": attempts[0].attempt_id})
            return PreparedResearchExecution(
                plan,
                execution,
                attempts[0],
                tuple(predictions),
                refreshed.portfolio_view,
                refreshed.action_plan_view,
                bound_spec,
            )

    def prepare_assessments(
        self, prepared: PreparedResearchExecution, bundle: SandboxExecutionBundle
    ) -> tuple[TestValidityAssessment, ...]:
        run = ResearchTestRun(
            execution=prepared.execution,
            attempt=prepared.attempt,
            spec=prepared.spec,
            result=bundle.result,
            receipt=bundle.receipt,
            observation_refs=tuple(span.span_id for span in bundle.observation.evidence_candidates),
        )
        return tuple(
            self._validity.prepare(prediction, self._current(prediction), run)
            for prediction in prepared.predictions
        )

    def complete(
        self,
        prepared: PreparedResearchExecution,
        bundle: SandboxExecutionBundle,
        assessments: tuple[TestValidityAssessment, ...],
    ) -> ResearchTestCompletion:
        observations = tuple(span.span_id for span in bundle.observation.evidence_candidates)
        self._execution.complete_sandbox_attempt(
            prepared.execution,
            prepared.plan,
            prepared.attempt,
            result=bundle.result,
            receipt=bundle.receipt,
            observation_refs=observations,
        )
        appraisals: list[JsonValue] = []
        final_commit: CommitResult | None = None
        for prediction, assessment in zip(prepared.predictions, assessments, strict=True):
            self._validity.persist(assessment)
            binding = self._hypothesis_service.bind_test(
                prediction,
                execution_ref=assessment.execution_ref,
                observation_refs=assessment.observation_refs,
                test_validity_assessment_ref=assessment.assessment_id,
                test_validity=assessment.test_validity,
                prediction_fit=assessment.prediction_fit,
            )
            appraisal, _current, final_commit = self._hypothesis_service.appraise(
                self._current(prediction),
                evidence_refs=observations,
                test_assessment_refs=(binding.test_binding_id,),
                appraisal_scope=assessment.scope,
            )
            appraisals.append(cast(JsonValue, appraisal.model_dump(mode="json")))
        if final_commit is None:
            raise ValueError("RESEARCH_TEST_ASSESSMENT_MISSING")
        projection: dict[str, JsonValue] = {
            "predictions": [item.model_dump(mode="json") for item in prepared.predictions],
            "assessments": [item.model_dump(mode="json") for item in assessments],
            "appraisals": appraisals,
            "execution_ref": prepared.execution.plan_execution_id,
            "attempt_ref": prepared.attempt.attempt_id,
            "plan_revision_digest": prepared.plan.revision_digest,
            "official_criterion_disposition": "NOT_ASSESSED",
            "semantic_truth_certified": False,
        }
        return ResearchTestCompletion(final_commit, projection)

    def _current(self, prediction: PredictionRecord) -> HypothesisRecord:
        current = self._hypotheses.read_hypothesis(
            prediction.project_id, prediction.hypothesis_id, None
        )
        if current is None:
            raise ValueError("RESEARCH_TEST_HYPOTHESIS_NOT_FOUND")
        return current

    def _reject_pending_result(
        self,
        project_id: str,
        action: ActionCandidate,
        spec: SandboxRunSpec,
    ) -> None:
        inputs = sorted(item.content_sha256 for item in spec.input_snapshots)
        for execution in self._executions.list_executions(project_id):
            if execution.object_id != action.object_id or execution.plan_id != spec.action_plan_id:
                continue
            revision = self._ledger.read_revision_by_digest(
                project_id, execution.plan_revision_digest
            )
            snapshot = (
                None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
            )
            if snapshot is None:
                raise ValueError("RESEARCH_TEST_PENDING_EXECUTION_UNRESOLVED")
            plan = ActionPlanRecord.model_validate(snapshot.content)
            for attempt in self._executions.list_attempts(project_id, execution.plan_execution_id):
                if (
                    attempt.state
                    not in {"DISPATCHED", "RUNNING", "CANCEL_REQUESTED", "UNKNOWN_COMPLETION"}
                    and attempt.observation_completeness != "NOT_ADMITTED"
                ):
                    continue
                step = next(
                    (
                        item
                        for item in plan.steps
                        if item["step_id"] == attempt.step_id
                        and item.get("action_id") == action.action_id
                    ),
                    None,
                )
                if step is None or sorted(attempt.input_digests) != inputs:
                    continue
                binding = step.get("runtime_binding")
                if not isinstance(binding, dict):
                    raise ValueError("RESEARCH_TEST_PENDING_EXECUTION_UNRESOLVED")
                if canonical_payload(cast(dict[str, object], binding)) != canonical_payload(
                    {
                        "runtime_profile": spec.runtime_profile.value,
                        "image_digest": spec.image_digest,
                        "argv": spec.argv,
                        "policy_digest": spec.policy_digest,
                    }
                ):
                    continue
                # Citation or hypothesis revisions do not authorize duplicate external work.
                raise ValueError("RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED")

    def hold(
        self, prepared: PreparedResearchExecution, bundle: SandboxExecutionBundle, reason: str
    ) -> None:
        current = self._executions.read_execution(
            prepared.execution.project_id, prepared.execution.plan_execution_id
        )
        attempt = self._executions.read_attempt(
            prepared.execution.project_id, prepared.attempt.attempt_id
        )
        if current is None or attempt is None:
            raise ValueError("RESEARCH_TEST_EXECUTION_NOT_FOUND")
        if attempt.state in {"DISPATCHED", "RUNNING"}:
            self._execution.complete_sandbox_attempt(
                current,
                prepared.plan,
                attempt,
                result=bundle.result,
                receipt=bundle.receipt,
                observation_refs=tuple(
                    span.span_id for span in bundle.observation.evidence_candidates
                ),
                observation_admitted=False,
                admission_reason=reason,
            )

    def retry(
        self, project: Project, action: ActionCandidate, prepared: PreparedResearchExecution
    ) -> PreparedResearchExecution:
        current = self._executions.read_execution(
            project.project_id, prepared.execution.plan_execution_id
        )
        if current is None:
            raise ValueError("RESEARCH_TEST_EXECUTION_NOT_FOUND")
        with self._ledger.transaction():
            execution, attempt, _commit = self._execution.retry(
                current,
                prepared.plan,
                step_id=prepared.attempt.step_id,
                failed_attempt_id=prepared.attempt.attempt_id,
                retry_reason="bounded transient research execution retry",
            )
            context = self._research.context(
                project.project_id,
                prepared.portfolio.object_id,
                dict(self._ledger.read_heads(project.project_id)),
            )
            if context.portfolio_view is None or context.action_plan_view is None:
                raise ValueError("RESEARCH_TEST_VIEW_UNAVAILABLE")
            spec = self._compiler.compile(
                project=project, plan=context.action_plan_view, action=action
            )
            return replace(
                prepared,
                execution=execution,
                attempt=attempt,
                spec=spec.model_copy(update={"attempt_id": attempt.attempt_id}),
                portfolio=context.portfolio_view,
                action_plan=context.action_plan_view,
            )
