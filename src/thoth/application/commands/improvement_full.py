from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands import improvement_control_drafts as drafts
from thoth.application.commands import improvement_control_views as views
from thoth.application.commands.behavior_execution import BehaviorExposureCommands
from thoth.application.commands.improvement_inputs import (
    EvaluationAssessInput,
    EvaluationPlanInput,
    ExposurePrepareInput,
    ImprovementReadInput,
    ListInput,
    PromotionDecideInput,
    PromotionPrepareInput,
    ProposeInput,
    RecordReadInput,
    RetireInput,
    ReviseInput,
    RollbackPrepareInput,
)
from thoth.application.commands.improvement_inputs import ProjectInput as ProjectInput
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_evidence import (
    assessments_match,
    plan_matches_improvement,
)
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.domain.auth import require_authenticated_authority
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import EvaluationRunError, PairedRunRecord
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

ALLOWED_TARGETS = {
    "PROMPT",
    "RETRIEVAL_QUERY_ROUTING",
    "CONTEXT_SELECTION_POLICY",
    "WORKFLOW_GATE_ORDER",
    "EVALUATOR_CODE",
    "TOOL_CONFIGURATION",
    "TRAINING_DATA_CANDIDATE",
}
PROHIBITED_TARGETS = {
    "PRODUCTION_MODEL_WEIGHTS",
    "OFFICIAL_KPI",
    "SAFETY_THRESHOLD",
    "WAIVER",
    "GO_OR_STOP_DECISION",
}


class ImprovementHandlers:
    def __init__(
        self,
        *,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        governance: GovernanceStorePort,
        ledger: LedgerPort,
        thread_scope: ImprovementThreadScope,
        paired: PairedEvaluationService | None = None,
        exposures: ImprovementExposureService | None = None,
    ) -> None:
        self._records = records
        self._controls = controls
        self._governance = governance
        self._ledger = ledger
        self._paired = paired
        self._thread_scope = thread_scope
        self._exposures = exposures
        self.exposure_commands = None if exposures is None else BehaviorExposureCommands(exposures)

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ListInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "IMPROVEMENT", "REVISION")
            if self._thread_scope.may_read(item)
            and (
                request.target_component is None
                or item.payload.get("target_component") == request.target_component
            )
            and (
                request.candidate_lifecycle is None
                or item.payload.get("candidate_lifecycle") == request.candidate_lifecycle
            )
            and (
                request.exposure_state is None
                or item.payload.get("exposure_state") == request.exposure_state
            )
            and (
                request.promotion_state is None
                or item.payload.get("promotion_state") == request.promotion_state
            )
        )
        return {"improvements": [item.model_dump(mode="json") for item in items]}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImprovementReadInput.model_validate(value)
        return {"improvement": self._improvement(request).model_dump(mode="json")}

    async def evaluation_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"evaluation": self._record(request, "EVALUATION_PLAN").model_dump(mode="json")}

    async def exposure_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"exposure": self._record(request, "EXPOSURE").model_dump(mode="json")}

    async def shadow_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"shadow": self._record(request, "SHADOW_RUN").model_dump(mode="json")}

    async def canary_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"canary": self._record(request, "CANARY_RUN").model_dump(mode="json")}

    async def promotion_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"promotion": self._record(request, "PROMOTION").model_dump(mode="json")}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImprovementReadInput.model_validate(value)
        self._improvement(request)
        records = tuple(
            item
            for item in self._records.list(
                request.project_id, "IMPROVEMENT", None, latest_only=False
            )
            if item.record_id == request.improvement_revision_id
            or item.payload.get("improvement_revision_id") == request.improvement_revision_id
        )
        return {"records": [item.model_dump(mode="json") for item in records]}

    async def propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ProposeInput.model_validate(value)
            self._thread_scope.require_thread(request.project_id, request.thread_id)
            if request.target_component in PROHIBITED_TARGETS:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "target has no autonomous Improvement path",
                )
            if request.target_component not in ALLOWED_TARGETS:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "unknown target")
            active = tuple(
                item
                for item in self._records.list(request.project_id, "IMPROVEMENT", "REVISION")
                if item.payload.get("scope_key") == request.scope_key
                and item.payload.get("target_component") == request.target_component
                and item.payload.get("candidate_lifecycle") != "RETIRED"
            )
            candidate_digest = domain_digest(
                "IMPROVEMENT_CANDIDATE",
                "1.0.0",
                canonical_payload(cast(dict[str, object], request.candidate_content)),
            )
            failed_same_digest = any(
                item.payload.get("candidate_digest") == candidate_digest
                and item.payload.get("performance_verdict") == "WORSE"
                for item in self._records.list(
                    request.project_id, "IMPROVEMENT", "REVISION", latest_only=False
                )
            )
            state = (
                "HELD_OVERLAP"
                if active
                else "REJECTED_FAILED_DIGEST"
                if failed_same_digest
                else "PROPOSED"
            )
            payload: dict[str, object] = {
                **drafts.proposal_fields(request, candidate_digest),
                "overlap_lock": tuple(
                    item.record_id for item in active if self._thread_scope.may_read(item)
                ),
            }
            record = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="REVISION",
                state=state,
                payload=payload,
            )
            return views.proposal_response(record, active)

    async def revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = ReviseInput.model_validate(value)
            current = self._improvement(request)
            if current.record_digest != request.expected_revision_digest:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision changed")
            allowed = {
                "candidate_content",
                "improvement_hypothesis",
                "evaluation_contract_ref",
                "scope_key",
            }
            rejected = sorted(set(request.patch) - allowed)
            if rejected:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    f"noncanonical Improvement patch: {', '.join(rejected)}",
                )
            payload = drafts.revised_payload(current, request)
            revised = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="REVISION",
                record_id=current.record_id,
                state="PROPOSED",
                payload=payload,
            )
            return views.revision_response(revised)

    async def evaluation_plan(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = EvaluationPlanInput.model_validate(value)
            improvement = self._improvement(
                ImprovementReadInput(
                    project_id=request.project_id,
                    improvement_revision_id=request.improvement_revision_id,
                )
            )
            hidden_exposure, self_evaluation, state = drafts.evaluation_plan_state(
                request, improvement
            )
            plan = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="EVALUATION_PLAN",
                state=state,
                payload=drafts.evaluation_plan_payload(
                    request, improvement, self_evaluation, hidden_exposure
                ),
            )
            return views.evaluation_plan_response(plan, self_evaluation, hidden_exposure)

    async def evaluation_assess(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvaluationAssessInput.model_validate(value)
        measured = self._measured_result(request)
        with self._ledger.transaction():
            plan = self._record(
                RecordReadInput(
                    project_id=request.project_id,
                    record_id=request.evaluation_plan_id,
                ),
                "EVALUATION_PLAN",
            )
            if plan.version != request.expected_evaluation_revision:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "evaluation revision changed"
                )
            guardrails = cast(tuple[dict[str, object], ...], plan.payload.get("guardrails", ()))
            critical_failure = any(
                item.get("status") == "FAIL" and item.get("critical") is True for item in guardrails
            )
            improvement = self._records.read(
                request.project_id,
                "IMPROVEMENT",
                str(plan.payload.get("improvement_revision_id", "")),
            )
            if measured is not None and self._paired is not None:
                self._paired.require_current(measured)
            validity, verdict = drafts.assessment_state(
                plan, improvement, measured, critical_failure
            )
            # Offline comparison is evidence, never actual canary or baseline approval.
            eligible = False
            assessed = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="EVALUATION_PLAN",
                record_id=plan.record_id,
                state="EVALUATED",
                payload=drafts.assessment_payload(
                    plan, request, validity, verdict, guardrails, critical_failure, eligible
                ),
            )

            return views.assessment_response(assessed, validity, verdict, guardrails, eligible)

    def _measured_result(self, request: EvaluationAssessInput) -> PairedRunRecord | None:
        if self._paired is None or len(request.result_refs) != 1:
            return None
        try:
            record = self._paired.require_assessment(request.project_id, request.result_refs[0])
        except EvaluationRunError:
            return None
        result = record.result
        if (
            result is None
            or request.evaluator_result_refs != (result.result_digest,)
            or request.exposure_ledger_ref != result.exposure_id
        ):
            return None
        return record

    async def exposure_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExposurePrepareInput.model_validate(value)
        if self._exposures is not None:
            proposal = self._improvement(
                ImprovementReadInput(
                    project_id=request.project_id,
                    improvement_revision_id=request.improvement_revision_id,
                )
            )
            plan = self._record(
                RecordReadInput(
                    project_id=request.project_id, record_id=request.evaluation_plan_id
                ),
                "EVALUATION_PLAN",
            )
            try:
                runtime = self._exposures.prepare(
                    project=request.project_id,
                    proposal=proposal,
                    plan=plan,
                    stage=request.requested_exposure_state,
                    scope=request.scope,
                    budget=request.duration_budget,
                    rollback=request.stop_rollback_contract,
                )
            except (BehaviorPolicyError, EvaluationRunError) as exc:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
                ) from exc
            if runtime is not None:
                projection = self._records.read(
                    request.project_id, "IMPROVEMENT", runtime.spec.exposure_id
                )
                assert projection is not None
                return views.runtime_exposure_response(projection, runtime)
        with self._ledger.transaction():
            request = ExposurePrepareInput.model_validate(value)
            improvement = self._improvement(
                ImprovementReadInput(
                    project_id=request.project_id,
                    improvement_revision_id=request.improvement_revision_id,
                )
            )
            evaluation = self._records.read(
                request.project_id, "IMPROVEMENT", request.evaluation_plan_id
            )
            if evaluation is None or evaluation.record_type != "EVALUATION_PLAN":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "evaluation plan not found")
            if not plan_matches_improvement(evaluation, improvement):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "exposure evaluation basis mismatch"
                )
            record_type = {
                "SHADOW": "SHADOW_RUN",
                "CANARY": "CANARY_RUN",
            }.get(request.requested_exposure_state, "EXPOSURE")
            requires_authority = request.requested_exposure_state == "CANARY"
            exposure = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type=record_type,
                state="PREPARED",
                payload=drafts.exposure_payload(
                    request, improvement, evaluation, requires_authority
                ),
            )
            return views.exposure_response(exposure)

    async def promotion_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = PromotionPrepareInput.model_validate(value)
            improvement = self._improvement(
                ImprovementReadInput(
                    project_id=request.project_id,
                    improvement_revision_id=request.improvement_revision_id,
                )
            )
            assessments = tuple(
                self._records.read(request.project_id, "IMPROVEMENT", ref)
                for ref in request.assessment_refs
            )
            eligible = assessments_match(assessments, improvement)
            runtime_payload: dict[str, object] = {}
            if eligible and self._exposures is not None:
                try:
                    runtime_payload = self._exposures.promotion_payload(assessments, improvement)
                except (BehaviorPolicyError, EvaluationRunError) as exc:
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
                    ) from exc
            digest_matches = request.candidate_digest == improvement.payload.get(
                "candidate_digest"
            ) and request.baseline_digest == improvement.payload.get("baseline_digest")
            scope_matches = request.target_scope == improvement.payload.get("scope_key")
            state = "PENDING" if eligible and digest_matches and scope_matches else "NOT_ELIGIBLE"
            promotion = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="PROMOTION",
                state=state,
                payload=drafts.promotion_payload(
                    request, improvement, assessments, runtime_payload
                ),
            )
            return views.promotion_response(promotion, state)

    async def promotion_decide(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PromotionDecideInput.model_validate(value)
        require_authenticated_authority(
            request.project_id, request.actor_ref, request.role_assignment_ref
        )
        runtime_evidence = None
        if self._exposures is not None and request.decision == "APPROVE":
            try:
                runtime_evidence = self._exposures.prepare_promotion(
                    request.project_id, request.promotion_candidate_id, request.approved_digest
                )
            except (BehaviorPolicyError, EvaluationRunError) as exc:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
                ) from exc
        with self._ledger.transaction():
            require_authenticated_authority(
                request.project_id, request.actor_ref, request.role_assignment_ref
            )
            promotion = self._record(
                RecordReadInput(
                    project_id=request.project_id,
                    record_id=request.promotion_candidate_id,
                ),
                "PROMOTION",
            )
            if promotion.state != "PENDING":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "promotion not eligible")
            if promotion.record_digest != request.approved_digest:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "approved digest mismatch")
            role = next(
                (
                    item
                    for item in self._governance.list_roles(request.project_id)
                    if item.role_assignment_id == request.role_assignment_ref
                    and item.actor_id == request.actor_ref
                    and item.role == "improvement-owner"
                    and item.state == "ACTIVE"
                ),
                None,
            )
            if role is None:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "authority role missing")
            improvement = self._records.read(
                request.project_id,
                "IMPROVEMENT",
                str(promotion.payload.get("improvement_revision_id", "")),
            )
            refs = promotion.payload.get("assessment_refs")
            assessments = (
                tuple(
                    self._records.read(request.project_id, "IMPROVEMENT", str(ref))
                    for ref in cast(list[object] | tuple[object, ...], refs)
                )
                if isinstance(refs, list | tuple)
                else ()
            )
            if request.decision == "APPROVE" and (
                improvement is None
                or improvement.record_digest != promotion.payload.get("improvement_record_digest")
                or not assessments_match(assessments, improvement)
                or {item.record_id: item.record_digest for item in assessments if item is not None}
                != promotion.payload.get("assessment_digests")
            ):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "promotion assessment basis changed"
                )
            decided = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="PROMOTION",
                record_id=promotion.record_id,
                state="APPROVED" if request.decision == "APPROVE" else "REJECTED",
                payload=drafts.promotion_decision_payload(promotion, request),
            )
            applied = None
            if self._exposures is not None and request.decision == "APPROVE":
                try:
                    applied = self._exposures.apply_promotion(decided, runtime_evidence)
                except (BehaviorPolicyError, EvaluationRunError) as exc:
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
                    ) from exc
            change_set = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="REVISION_CHANGE_SET_PROPOSAL",
                state="PROPOSED_NOT_APPLIED" if applied is None else "APPLIED_BEHAVIOR_BASELINE",
                payload=drafts.baseline_change_payload(decided, request, applied),
            )
            return views.promotion_decision_response(decided, change_set, applied)

    async def rollback_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = RollbackPrepareInput.model_validate(value)
            rollback = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="ROLLBACK",
                state="PREPARED",
                payload=drafts.rollback_payload(request),
            )
            return views.rollback_response(rollback)

    async def retire_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = RetireInput.model_validate(value)
            current = self._improvement(request)
            if current.record_digest != request.expected_revision_digest:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision changed")
            retirement = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="RETIRE_CANDIDATE",
                state="PROPOSED_NOT_APPLIED",
                payload=drafts.retirement_payload(current, request),
            )
            return {"retirement": retirement.model_dump(mode="json"), "history_preserved": True}

    def _improvement(self, request: ImprovementReadInput) -> ControlRecord:
        item = self._records.read(
            request.project_id, "IMPROVEMENT", request.improvement_revision_id
        )
        if item is None or item.record_type != "REVISION":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "improvement not found")
        self._thread_scope.require_record(item)
        return item

    def _record(self, request: RecordReadInput, record_type: str) -> ControlRecord:
        item = self._records.read(request.project_id, "IMPROVEMENT", request.record_id)
        if item is None or item.record_type != record_type:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, f"{record_type} not found")
        self._thread_scope.require_record(item)
        return item
