from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.commands.behavior_execution import BehaviorExposureCommands
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_evidence import (
    assessments_match,
    plan_matches_improvement,
)
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.domain.auth import require_authenticated_authority
from thoth.domain.base import DomainModel
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


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ListInput(ProjectInput):
    target_component: str | None = Field(default=None, max_length=160)
    candidate_lifecycle: str | None = Field(default=None, max_length=80)
    exposure_state: str | None = Field(default=None, max_length=80)
    promotion_state: str | None = Field(default=None, max_length=80)


class ImprovementReadInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class RecordReadInput(ProjectInput):
    record_id: str = Field(min_length=1, max_length=160)


class ProposeInput(ProjectInput):
    thread_id: str | None = Field(default=None, min_length=1, max_length=160)
    target_component: str = Field(min_length=1, max_length=160)
    scope_key: str = Field(min_length=1, max_length=500)
    trigger_refs: tuple[str, ...]
    baseline_digest: str = Field(min_length=64, max_length=64)
    candidate_content: dict[str, JsonValue]
    improvement_hypothesis: str = Field(min_length=1, max_length=5_000)
    evaluation_contract_ref: str = Field(min_length=1, max_length=160)


class ReviseInput(ImprovementReadInput):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class EvaluationPlanInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    baseline_digest: str = Field(min_length=64, max_length=64)
    datasets: tuple[dict[str, JsonValue], ...]
    evaluator_refs: tuple[str, ...]
    guardrails: tuple[dict[str, JsonValue], ...]
    exposure_policy_ref: str = Field(min_length=1, max_length=160)


class EvaluationAssessInput(ProjectInput):
    evaluation_plan_id: str = Field(min_length=1, max_length=160)
    result_refs: tuple[str, ...]
    evaluator_result_refs: tuple[str, ...]
    exposure_ledger_ref: str = Field(min_length=1, max_length=160)
    expected_evaluation_revision: int = Field(ge=1)


class ExposurePrepareInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    requested_exposure_state: str = Field(pattern=r"^(OFFLINE|SANDBOX|SHADOW|CANARY)$")
    evaluation_plan_id: str = Field(min_length=1, max_length=160)
    scope: dict[str, str]
    duration_budget: dict[str, JsonValue]
    stop_rollback_contract: dict[str, JsonValue]


class PromotionPrepareInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    assessment_refs: tuple[str, ...]
    candidate_digest: str = Field(min_length=64, max_length=64)
    baseline_digest: str = Field(min_length=64, max_length=64)
    target_scope: str = Field(min_length=1, max_length=500)
    policy_version: str = Field(min_length=1, max_length=160)


class PromotionDecideInput(ProjectInput):
    promotion_candidate_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    role_assignment_ref: str = Field(min_length=1, max_length=160)
    approved_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)


class RollbackPrepareInput(ProjectInput):
    promoted_revision_ref: str = Field(min_length=1, max_length=160)
    guardrail_failure_refs: tuple[str, ...]
    rollback_target_digest: str = Field(min_length=64, max_length=64)
    target_scope: str = Field(min_length=1, max_length=500)


class RetireInput(ImprovementReadInput):
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...]
    expected_revision_digest: str = Field(min_length=64, max_length=64)


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
                "thread_id": request.thread_id,
                "target_component": request.target_component,
                "scope_key": request.scope_key,
                "trigger_refs": request.trigger_refs,
                "baseline_digest": request.baseline_digest,
                "candidate_content": cast(dict[str, object], request.candidate_content),
                "candidate_digest": candidate_digest,
                "improvement_hypothesis": request.improvement_hypothesis,
                "evaluation_contract_ref": request.evaluation_contract_ref,
                "candidate_lifecycle": "PROPOSED",
                "evaluation_validity": "NOT_ASSESSED",
                "performance_verdict": "INCONCLUSIVE",
                "exposure_state": "NONE",
                "promotion_state": "NOT_ELIGIBLE",
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
            return {
                "improvement": record.model_dump(mode="json"),
                "overlap_lock_check": not active,
                "prohibited_target_check": True,
            }

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
            payload = {
                **current.payload,
                **cast(dict[str, object], request.patch),
                "evaluation_validity": "NOT_ASSESSED",
                "performance_verdict": "INCONCLUSIVE",
                "promotion_state": "NOT_ELIGIBLE",
                "evidence_refs": request.evidence_refs,
                "revision_reason": request.reason,
            }
            if "candidate_content" in request.patch:
                content = cast(dict[str, object], request.patch["candidate_content"])
                payload["candidate_digest"] = domain_digest(
                    "IMPROVEMENT_CANDIDATE",
                    "1.0.0",
                    canonical_payload(content),
                )
            revised = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="REVISION",
                record_id=current.record_id,
                state="PROPOSED",
                payload=payload,
            )
            return {
                "improvement": revised.model_dump(mode="json"),
                "old_evaluation_invalidated": True,
            }

    async def evaluation_plan(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = EvaluationPlanInput.model_validate(value)
            improvement = self._improvement(
                ImprovementReadInput(
                    project_id=request.project_id,
                    improvement_revision_id=request.improvement_revision_id,
                )
            )
            hidden_exposure = any(
                item.get("contains_expected_values") is True for item in request.datasets
            )
            self_evaluation = improvement.payload.get(
                "target_component"
            ) == "EVALUATOR_CODE" and any(
                ref == improvement.payload.get("candidate_digest") for ref in request.evaluator_refs
            )
            state = (
                "HELD"
                if hidden_exposure
                or self_evaluation
                or request.baseline_digest != improvement.payload.get("baseline_digest")
                else "PLANNED"
            )
            plan = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="EVALUATION_PLAN",
                state=state,
                payload={
                    "improvement_revision_id": improvement.record_id,
                    "improvement_record_digest": improvement.record_digest,
                    "baseline_digest": request.baseline_digest,
                    "candidate_digest": improvement.payload.get("candidate_digest"),
                    "datasets": request.datasets,
                    "evaluator_refs": request.evaluator_refs,
                    "guardrails": request.guardrails,
                    "exposure_policy_ref": request.exposure_policy_ref,
                    "evaluator_isolation": not self_evaluation,
                    "hidden_expected_value_access": hidden_exposure,
                    "evaluation_revision": 1,
                    "results": (),
                },
            )
            return {
                "evaluation_plan": plan.model_dump(mode="json"),
                "evaluator_isolation": not self_evaluation,
                "holdout_access_check": not hidden_exposure,
                "action_plan_candidates": [],
            }

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
            pair = None if measured is None else measured.result
            bound = (
                pair is not None
                and improvement is not None
                and plan_matches_improvement(plan, improvement)
            )
            if bound and measured is not None:
                bound = (
                    measured.spec.plan_id == plan.record_id
                    and measured.spec.plan_digest == plan.record_digest
                    and measured.spec.baseline_digest == plan.payload.get("baseline_digest")
                    and measured.spec.candidate_digest == plan.payload.get("candidate_digest")
                )
            validity = (
                "INVALID"
                if plan.state == "HELD"
                else pair.validity
                if bound and pair is not None
                else "INCONCLUSIVE"
            )
            verdict = (
                "INCONCLUSIVE"
                if validity != "VALID"
                else "WORSE"
                if critical_failure
                else pair.performance_verdict
                if pair is not None
                else "INCONCLUSIVE"
            )
            # Offline comparison is evidence, never actual canary or baseline approval.
            eligible = False
            assessed = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="EVALUATION_PLAN",
                record_id=plan.record_id,
                state="EVALUATED",
                payload={
                    **plan.payload,
                    "evaluation_revision": plan.version + 1,
                    "result_refs": request.result_refs,
                    "evaluator_result_refs": request.evaluator_result_refs,
                    "exposure_ledger_ref": request.exposure_ledger_ref,
                    "evaluation_validity": validity,
                    "performance_verdict": verdict,
                    "guardrail_vector": guardrails,
                    "critical_guardrail_failure": critical_failure,
                    "promotion_eligible": eligible,
                },
            )

            return cast(
                dict[str, JsonValue],
                {
                    "evaluation": assessed.model_dump(mode="json"),
                    "evaluation_validity": validity,
                    "performance_verdict": verdict,
                    "guardrail_vector": list(guardrails),
                    "promotion_eligibility": eligible,
                },
            )

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
                return {
                    "exposure": projection.model_dump(mode="json"),
                    "runtime_exposure": runtime.model_dump(mode="json"),
                    "approval_target_digest": runtime.spec.spec_digest,
                    "policy_authorization_requirements": "PROTECTED_R3"
                    if runtime.spec.stage == "CANARY"
                    else "R0_R2_BOUNDED",
                    "actual_execution_observed": False,
                }
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
                payload={
                    "improvement_revision_id": improvement.record_id,
                    "evaluation_plan_id": evaluation.record_id,
                    "requested_exposure_state": request.requested_exposure_state,
                    "scope": request.scope,
                    "duration_budget": request.duration_budget,
                    "stop_rollback_contract": request.stop_rollback_contract,
                    "output_authoritative": False,
                    "policy_requirements": (
                        "PROTECTED_R3" if requires_authority else "R0_R2_BOUNDED"
                    ),
                    "authorization_required": requires_authority,
                },
            )
            return cast(
                dict[str, JsonValue],
                {
                    "exposure": exposure.model_dump(mode="json"),
                    "action_plan_candidate": {"owner": "ACTION", "automatic_execution": False},
                    "policy_authorization_requirements": exposure.payload["policy_requirements"],
                },
            )

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
                payload={
                    "improvement_revision_id": improvement.record_id,
                    "improvement_record_digest": improvement.record_digest,
                    "assessment_refs": request.assessment_refs,
                    "assessment_digests": {
                        item.record_id: item.record_digest
                        for item in assessments
                        if item is not None
                    },
                    "candidate_digest": request.candidate_digest,
                    "baseline_digest": request.baseline_digest,
                    "target_scope": request.target_scope,
                    "policy_version": request.policy_version,
                    "required_roles": ("improvement-owner",),
                    **runtime_payload,
                    "exact_impact": {
                        "target_component": improvement.payload.get("target_component"),
                        "scope_key": improvement.payload.get("scope_key"),
                    },
                },
            )
            return cast(
                dict[str, JsonValue],
                {
                    "promotion": promotion.model_dump(mode="json"),
                    "eligibility": state,
                    "exact_impact": promotion.payload["exact_impact"],
                },
            )

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
                payload={
                    **promotion.payload,
                    "decision": request.decision,
                    "actor_ref": request.actor_ref,
                    "role_assignment_ref": request.role_assignment_ref,
                    "reason": request.reason,
                },
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
                payload={
                    "promotion_id": decided.record_id,
                    "baseline_change": request.decision == "APPROVE",
                    "current_state_mutated": applied is not None,
                    "baseline_kind": None if applied is None else "BEHAVIOR",
                    "behavior_baseline_receipt": None
                    if applied is None
                    else applied.receipt_digest,
                },
            )
            return {
                "promotion": decided.model_dump(mode="json"),
                "revision_baseline_change_set_proposal": change_set.model_dump(mode="json"),
                "current_state_mutated": applied is not None,
                "behavior_baseline_revision": None
                if applied is None
                else applied.model_dump(mode="json"),
            }

    async def rollback_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = RollbackPrepareInput.model_validate(value)
            rollback = self._controls.create(
                project_id=request.project_id,
                namespace="IMPROVEMENT",
                record_type="ROLLBACK",
                state="PREPARED",
                payload={
                    "promoted_revision_ref": request.promoted_revision_ref,
                    "guardrail_failure_refs": request.guardrail_failure_refs,
                    "rollback_target_digest": request.rollback_target_digest,
                    "target_scope": request.target_scope,
                    "authority": (
                        "PREAUTHORIZED" if request.guardrail_failure_refs else "PROTECTED_R3"
                    ),
                    "exact_rollback_claimed": False,
                    "residual_state": "REQUIRES_REASSESSMENT",
                    "current_state_mutated": False,
                },
            )
            return cast(
                dict[str, JsonValue],
                {
                    "rollback": rollback.model_dump(mode="json"),
                    "action_revision_change_set": {"automatic_execution": False},
                    "residual_state": rollback.payload["residual_state"],
                },
            )

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
                payload={
                    "improvement_revision_id": current.record_id,
                    "reason": request.reason,
                    "evidence_refs": request.evidence_refs,
                    "exposure_cleanup": "REQUIRED",
                    "retention_impact": "PRESERVE_EVALUATION_HISTORY",
                },
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
