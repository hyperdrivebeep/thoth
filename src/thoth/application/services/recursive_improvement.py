from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from pydantic import AwareDatetime

from thoth.application.services import recursive_improvement_records as records
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.evaluation_producer import EvaluationProducer, SelectedEvaluation
from thoth.application.services.improvement_candidate_generator import (
    BoundedBehaviorCandidateGenerator,
)
from thoth.application.services.improvement_evaluation_gate import ImprovementEvaluationGate
from thoth.application.services.improvement_observation_results import ImprovementObservationResults
from thoth.application.services.improvement_runner import ImprovementRunner
from thoth.domain.behavior_artifact import BehaviorArtifact, BehaviorArtifactKind
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import EvaluationRunError, PairedRunRecord
from thoth.domain.evaluator import ImprovementEvaluationUnavailable
from thoth.domain.improvement import (
    ImprovementEvaluation,
    ImprovementEvaluationRequest,
    ImprovementExposure,
    ImprovementRunState,
    RecursiveImprovementResult,
)
from thoth.domain.improvement_observation import ImprovementObservation
from thoth.domain.research_execution import research_work
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.improvement import ImprovementEvaluatorPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort


@dataclass(frozen=True)
class PreparedImprovement:
    request: ImprovementEvaluationRequest
    request_record: ControlRecord
    fingerprint: str
    count: int
    behavior_baseline: BehaviorArtifact | None
    behavior_candidate: BehaviorArtifact | None


@dataclass(frozen=True)
class PreparedPair:
    selected: SelectedEvaluation
    fingerprint: str
    count: int
    thread_id: str


class RecursiveImprovementCoordinator:
    def __init__(
        self,
        *,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        evaluator: ImprovementEvaluatorPort,
        unit_of_work: AtomicUnitOfWorkPort,
        ledger: LedgerPort,
        evaluation_gate: ImprovementEvaluationGate,
        clock: ClockPort,
        ids: IdGeneratorPort,
        threshold: int = 2,
        baselines: BaselineService | None = None,
        behavior_candidates: BehaviorCandidateService | None = None,
        improvement_runner: ImprovementRunner | None = None,
        producer: EvaluationProducer | None = None,
        candidate_generator: BoundedBehaviorCandidateGenerator | None = None,
    ) -> None:
        self._records = records
        self._controls = controls
        self._evaluator = evaluator
        self._unit_of_work = unit_of_work
        self._ledger = ledger
        self._gate = evaluation_gate
        self._clock = clock
        self._ids = ids
        self._threshold = threshold
        self._baselines = baselines
        self._behavior_candidates = behavior_candidates
        self._improvement_runner = improvement_runner
        self._producer = producer
        self._candidate_generator = candidate_generator
        self._observation_results = ImprovementObservationResults(records, controls, ledger)

    async def observe(
        self,
        *,
        project_id: str,
        thread_id: str,
        r2_projection: dict[str, object] | None,
    ) -> RecursiveImprovementResult | None:
        event = self._failure_event(project_id, thread_id, r2_projection)
        if event is not None:
            previous = self._observation_results.read(project_id, thread_id, event)
            if previous is not None:
                return previous
        result = await self._observe(
            project_id=project_id, thread_id=thread_id, r2_projection=r2_projection
        )
        if event is not None and result is not None:
            self._observation_results.save(event, result)
        return result

    @staticmethod
    def _failure_event(
        project: str, thread: str, projection: dict[str, object] | None
    ) -> str | None:
        if projection is None or projection.get("terminal_state") == "COMPLETED":
            return None
        recovery = projection.get("recovery")
        attempts = (
            cast(dict[str, object], recovery).get("attempts")
            if isinstance(recovery, dict)
            else None
        )
        if not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict):
            return None
        last = cast(dict[str, object], attempts[-1])
        attempt = last.get("attempt_id")
        if not isinstance(attempt, str) or not attempt:
            return None
        work = research_work.get()
        return domain_digest(
            "FAILURE_EVENT",
            "1.0.0",
            canonical_payload(
                {
                    "project": project,
                    "thread": thread,
                    "attempt": attempt,
                    "request": None if work is None else work.request_ref,
                }
            ),
        )

    def observation(
        self,
        project: str,
        thread: str,
        projection: dict[str, object] | None,
        result: RecursiveImprovementResult | None,
    ) -> ImprovementObservation:
        if result is None:
            return records.untriggered_observation(projection)
        event = self._failure_event(project, thread, projection)
        return records.result_observation(result, event)

    async def _observe(
        self,
        *,
        project_id: str,
        thread_id: str,
        r2_projection: dict[str, object] | None,
    ) -> RecursiveImprovementResult | None:
        prepared = self._prepare(
            project_id=project_id, thread_id=thread_id, r2_projection=r2_projection
        )
        if prepared is None or isinstance(prepared, RecursiveImprovementResult):
            return prepared
        if isinstance(prepared, PreparedPair):
            return await self._evaluate_selected(prepared)
        request = prepared.request
        try:
            evaluation = self._evaluator.evaluate(
                baseline_digest=request.baseline_digest,
                candidate_digest=request.candidate_digest,
                fixture_digest=request.fixture_digest,
                hidden_holdout_digest=request.hidden_holdout_digest,
                max_requests=request.max_requests,
                timeout_seconds=request.timeout_seconds,
            )
            return self._finalize(prepared, evaluation)
        except ImprovementEvaluationUnavailable as exc:
            return self._hold_unavailable(prepared, exc.code)
        except Exception:
            self._gate.transition(prepared.request_record, "FAILED")
            raise

    async def _evaluate_selected(self, prepared: PreparedPair) -> RecursiveImprovementResult:
        assert self._producer is not None
        plan = prepared.selected.plan
        run: PairedRunRecord | None = None
        reason = None
        try:
            run = await self._producer.run(prepared.selected)
        except (EvaluationRunError, BehaviorPolicyError) as exc:
            reason = exc.code
        except asyncio.CancelledError:
            if self._candidate_generator is not None:
                with self._ledger.transaction():
                    self._candidate_generator.record_evaluation(plan, None, "EVALUATION_CANCELLED")
            raise
        except Exception:
            if self._candidate_generator is not None:
                with self._ledger.transaction():
                    self._candidate_generator.record_evaluation(
                        plan, None, "EVALUATION_INTERRUPTED"
                    )
            raise
        if run is not None and run.state != "COMPLETE":
            reason = run.reason_code or "EVALUATION_INCOMPLETE"
        with self._ledger.transaction(), self._unit_of_work.transaction():
            baseline = str(plan.payload["baseline_digest"])
            if self._candidate_generator is not None:
                self._candidate_generator.record_evaluation(plan, run, reason)
            candidate = str(plan.payload["candidate_digest"])
            result = self._result(
                project_id=plan.project_id,
                thread_id=prepared.thread_id,
                state=ImprovementRunState.EVALUATED
                if reason is None and run is not None
                else ImprovementRunState.HELD,
                fingerprint=prepared.fingerprint,
                count=prepared.count,
                baseline=baseline,
                candidate=candidate,
                fixture=None if run is None else run.spec.fixture_digest,
                active=baseline,
                rollback_reason=reason,
                now=self._clock.now(),
            )
            updated = records.paired_result(result, run)
            return self._persist_result(updated)

    def _hold_unavailable(
        self, prepared: PreparedImprovement, code: str
    ) -> RecursiveImprovementResult:
        request = prepared.request
        with self._ledger.transaction(), self._unit_of_work.transaction():
            self._gate.require_pending(prepared.request_record)
            result = self._result(
                project_id=request.project_id,
                thread_id=request.thread_id,
                state=ImprovementRunState.HELD,
                fingerprint=prepared.fingerprint,
                count=prepared.count,
                baseline=request.baseline_digest,
                candidate=request.candidate_digest,
                fixture=request.fixture_digest,
                active=self._gate.current_active_digest(
                    request.project_id, request.baseline_digest
                ),
                rollback_reason=code,
                now=self._clock.now(),
            )
            self._gate.transition(prepared.request_record, "HELD")
            return self._persist_result(result)

    def _prepare(
        self,
        *,
        project_id: str,
        thread_id: str,
        r2_projection: dict[str, object] | None,
    ) -> PreparedImprovement | PreparedPair | RecursiveImprovementResult | None:
        if r2_projection is None or r2_projection.get("terminal_state") == "COMPLETED":
            return None
        recovery = r2_projection.get("recovery")
        if not isinstance(recovery, dict):
            return None
        recovery_values = cast(dict[str, object], recovery)
        attempts = recovery_values.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            return None
        last = cast(list[object], attempts)[-1]
        if not isinstance(last, dict):
            return None
        last_values = cast(dict[str, object], last)
        failure_class = str(last_values.get("failure_class") or "UNKNOWN")
        detail = str(last_values.get("failure_detail") or "")
        fingerprint = records.failure_fingerprint(failure_class, detail, r2_projection)
        with self._ledger.transaction(), self._unit_of_work.transaction():
            baseline = (
                None
                if self._baselines is None
                else self._baselines.composite_current_digest(project_id)
            ) or str(recovery_values.get("baseline_head_before") or "0" * 64)
            event = self._failure_event(project_id, thread_id, r2_projection)
            if event is None:
                return None
            previous = self._records.read(project_id, "IMPROVEMENT_RUNTIME", f"failure:{event}")
            if previous is not None:
                return self._result(
                    project_id=project_id,
                    thread_id=thread_id,
                    state=ImprovementRunState.HELD,
                    fingerprint=fingerprint,
                    count=int(str(previous.payload["ordinal"])),
                    baseline=baseline,
                    active=baseline,
                    rollback_reason="FAILURE_EVENT_ALREADY_OBSERVED",
                    now=self._clock.now(),
                )
            observations = tuple(
                item
                for item in self._records.list(
                    project_id,
                    "IMPROVEMENT_RUNTIME",
                    "FAILURE_OBSERVATION",
                    latest_only=False,
                )
                if item.payload.get("failure_fingerprint") == fingerprint
            )
            count = len(observations) + 1
            now = self._clock.now()
            self._controls.create(
                project_id=project_id,
                namespace="IMPROVEMENT_RUNTIME",
                record_type="FAILURE_OBSERVATION",
                record_id=f"failure:{event}",
                state="OBSERVED",
                payload=records.failure_observation_payload(
                    thread_id, fingerprint, failure_class, detail, baseline, count
                ),
            )
            if count < self._threshold:
                result = self._result(
                    project_id=project_id,
                    thread_id=thread_id,
                    state=ImprovementRunState.BELOW_THRESHOLD,
                    fingerprint=fingerprint,
                    count=count,
                    baseline=baseline,
                    active=baseline,
                    now=now,
                )
                self._observation_results.save(event, result)
                return result
            return self._prepare_ready(
                project_id, thread_id, fingerprint, count, baseline, now, failure_class
            )

    def _prepare_ready(
        self,
        project_id: str,
        thread_id: str,
        fingerprint: str,
        count: int,
        baseline: str,
        now: AwareDatetime,
        failure_class: str,
    ) -> PreparedImprovement | PreparedPair | RecursiveImprovementResult:
        if self._producer is not None:
            try:
                selected = self._producer.select(project_id, thread_id)
                if selected is None and self._candidate_generator is not None:
                    selected = self._candidate_generator.generate(
                        project_id, thread_id, fingerprint, failure_class
                    )
            except (EvaluationRunError, BehaviorPolicyError) as exc:
                return self._persist_result(
                    self._result(
                        project_id=project_id,
                        thread_id=thread_id,
                        state=ImprovementRunState.HELD,
                        fingerprint=fingerprint,
                        count=count,
                        baseline=baseline,
                        active=self._gate.current_active_digest(project_id, baseline),
                        rollback_reason=exc.code,
                        now=now,
                    )
                )
            if selected is not None:
                return PreparedPair(selected, fingerprint, count, thread_id)
            if self._candidate_generator is not None:
                return self._persist_result(
                    self._result(
                        project_id=project_id,
                        thread_id=thread_id,
                        state=ImprovementRunState.HELD,
                        fingerprint=fingerprint,
                        count=count,
                        baseline=baseline,
                        active=self._gate.current_active_digest(project_id, baseline),
                        rollback_reason="BEHAVIOR_CANDIDATE_NOT_AVAILABLE",
                        now=now,
                    )
                )
        return self._prepare_legacy(
            project_id, thread_id, fingerprint, count, baseline, now, failure_class
        )

    def _prepare_legacy(
        self,
        project_id: str,
        thread_id: str,
        fingerprint: str,
        count: int,
        baseline: str,
        now: AwareDatetime,
        failure_class: str,
    ) -> PreparedImprovement | RecursiveImprovementResult:
        candidate_draft, candidate_digest = records.legacy_candidate(fingerprint, failure_class)
        behavior_baseline = None
        behavior_candidate = None
        if self._behavior_candidates is not None:
            behavior_baseline = self._gate.active_behavior_baseline(project_id)
            if behavior_baseline is None:
                behavior_baseline = self._behavior_candidates.ensure_baseline(
                    project_id,
                    BehaviorArtifactKind.WORKFLOW_DEFINITION,
                )
            behavior_candidate = self._behavior_candidates.create_candidate(
                project_id=project_id,
                kind=BehaviorArtifactKind.WORKFLOW_DEFINITION,
                content=candidate_draft,
                parent_digest=behavior_baseline.content_digest,
            )
            candidate_digest = behavior_candidate.content_digest
        prior_failed = next(
            (
                item
                for item in (
                    *self._records.list(
                        project_id, "IMPROVEMENT_RUNTIME", "RUN", latest_only=False
                    ),
                    *self._records.list(project_id, "IMPROVEMENT_RUNTIME", "EVALUATION_REQUEST"),
                )
                if item.payload.get("candidate_digest") == candidate_digest
                and (
                    item.record_type == "EVALUATION_REQUEST"
                    or item.state in {"ROLLED_BACK", "REJECTED_SAME_DIGEST"}
                )
            ),
            None,
        )
        if prior_failed is not None:
            active = self._gate.current_active_digest(project_id, baseline)
            if (
                behavior_baseline is not None
                and active == behavior_baseline.content_digest
                and active != candidate_digest
            ):
                active = baseline
            return self._persist_result(
                self._result(
                    project_id=project_id,
                    thread_id=thread_id,
                    state=ImprovementRunState.REJECTED_SAME_DIGEST,
                    fingerprint=fingerprint,
                    count=count,
                    baseline=baseline,
                    candidate=candidate_digest,
                    active=active,
                    rollback_reason="SAME_DIGEST_RETRY_PROHIBITED",
                    now=now,
                )
            )
        fixture_digest, hidden_holdout_digest = records.fixture_digests(fingerprint)
        request_draft: dict[str, object] = {
            "request_id": self._ids.new("improvement-evaluation"),
            "project_id": project_id,
            "thread_id": thread_id,
            "baseline_digest": baseline,
            "candidate_digest": candidate_digest,
            "fixture_digest": fixture_digest,
            "hidden_holdout_digest": hidden_holdout_digest,
            "context": self._gate.capture(project_id),
            "max_requests": 8,
            "timeout_seconds": 60,
            "created_at": now,
        }
        request = records.seal_evaluation_request(request_draft)
        record = self._controls.create(
            project_id=project_id,
            namespace="IMPROVEMENT_RUNTIME",
            record_type="EVALUATION_REQUEST",
            record_id=request.request_id,
            state="EVALUATING",
            payload=request.model_dump(mode="python"),
        )
        return PreparedImprovement(
            request, record, fingerprint, count, behavior_baseline, behavior_candidate
        )

    def _finalize(
        self,
        prepared: PreparedImprovement,
        evaluation: ImprovementEvaluation,
    ) -> RecursiveImprovementResult:
        request = prepared.request
        project_id, thread_id = request.project_id, request.thread_id
        baseline, candidate_digest, fixture_digest = (
            request.baseline_digest,
            request.candidate_digest,
            request.fixture_digest,
        )
        fingerprint, count = prepared.fingerprint, prepared.count
        behavior_baseline = prepared.behavior_baseline
        behavior_candidate = prepared.behavior_candidate
        with self._ledger.transaction(), self._unit_of_work.transaction():
            self._gate.require_pending(prepared.request_record)
            if not self._gate.is_current(request):
                result = self._result(
                    project_id=project_id,
                    thread_id=thread_id,
                    state=ImprovementRunState.HELD,
                    fingerprint=fingerprint,
                    count=count,
                    baseline=baseline,
                    candidate=candidate_digest,
                    fixture=fixture_digest,
                    active=self._gate.current_active_digest(project_id, baseline),
                    rollback_reason="CONTEXT_CHANGED",
                    now=self._clock.now(),
                )
                self._gate.transition(prepared.request_record, "HELD")
                return self._persist_result(result)
            now = self._clock.now()
            reason = (
                "TIMEOUT"
                if (now - request.created_at).total_seconds() >= request.timeout_seconds
                else self._gate.binding_error(request, evaluation)
                or self._rollback_reason(evaluation)
            )
            success = reason is None
            exposures = self._exposures(
                project_id=project_id,
                baseline=baseline,
                candidate=candidate_digest,
                fixture=fixture_digest,
                evaluator_id=evaluation.evaluator_id,
                include_canary=success,
                now=now,
            )
            result = self._result(
                project_id=project_id,
                thread_id=thread_id,
                state=(
                    ImprovementRunState.PROMOTED_LOCAL
                    if success
                    else ImprovementRunState.ROLLED_BACK
                ),
                fingerprint=fingerprint,
                count=count,
                baseline=baseline,
                candidate=candidate_digest,
                fixture=fixture_digest,
                evaluator_id=evaluation.evaluator_id,
                evaluation=evaluation,
                exposures=exposures,
                rollback_reason=reason,
                active=candidate_digest if success else baseline,
                exact_digest_canary=success,
                now=now,
            )
            if (
                self._improvement_runner is not None
                and behavior_baseline is not None
                and behavior_candidate is not None
            ):
                registry = (
                    self._improvement_runner.promote(
                        behavior_baseline,
                        behavior_candidate,
                    )
                    if success
                    else self._improvement_runner.rollback(
                        behavior_baseline,
                        behavior_candidate,
                    )
                )
                result = records.behavior_result(result, behavior_candidate, registry)
            for exposure in exposures:
                self._controls.create(
                    project_id=project_id,
                    namespace="IMPROVEMENT_RUNTIME",
                    record_type="EXPOSURE",
                    record_id=exposure.exposure_id,
                    state=exposure.stage,
                    payload=exposure.model_dump(mode="python"),
                )
            self._gate.transition(prepared.request_record, "FINALIZED")
            return self._persist_result(result)

    def _persist_result(self, result: RecursiveImprovementResult) -> RecursiveImprovementResult:
        self._controls.create(
            project_id=result.project_id,
            namespace="IMPROVEMENT_RUNTIME",
            record_type="RUN",
            record_id=result.run_id,
            state=result.state.value,
            payload=result.model_dump(mode="python"),
        )
        return result

    def _result(
        self,
        *,
        project_id: str,
        thread_id: str,
        state: ImprovementRunState,
        fingerprint: str,
        count: int,
        baseline: str,
        active: str,
        now: AwareDatetime,
        candidate: str | None = None,
        fixture: str | None = None,
        evaluator_id: str | None = None,
        evaluation: ImprovementEvaluation | None = None,
        exposures: tuple[ImprovementExposure, ...] = (),
        rollback_reason: str | None = None,
        exact_digest_canary: bool = False,
    ) -> RecursiveImprovementResult:
        draft: dict[str, object] = {
            "run_id": self._ids.new("improvement-run"),
            **records.result_fields(
                project_id,
                thread_id,
                state,
                fingerprint,
                count,
                baseline,
                candidate,
                fixture,
                evaluator_id,
                evaluation,
                exposures,
                rollback_reason,
                active,
                exact_digest_canary,
                now,
            ),
        }
        return records.seal_result(draft)

    def _exposures(
        self,
        *,
        project_id: str,
        baseline: str,
        candidate: str,
        fixture: str,
        evaluator_id: str,
        include_canary: bool,
        now: AwareDatetime,
    ) -> tuple[ImprovementExposure, ...]:
        stages = ("OFFLINE", "SANDBOX", "SHADOW", "CANARY") if include_canary else ("OFFLINE",)
        values: list[ImprovementExposure] = []
        for stage in stages:
            draft: dict[str, object] = {
                "exposure_id": self._ids.new("improvement-exposure"),
                "stage": stage,
                "project_id": project_id,
                "baseline_digest": baseline,
                "candidate_digest": candidate,
                "fixture_digest": fixture,
                "evaluator_id": evaluator_id,
                "exact_candidate_digest": True,
                "hidden_holdout_accessed": False,
                "external_effect": False,
                "recorded_at": now,
            }
            values.append(records.seal_exposure(draft))
        return tuple(values)

    @staticmethod
    def _rollback_reason(evaluation: ImprovementEvaluation) -> str | None:
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
