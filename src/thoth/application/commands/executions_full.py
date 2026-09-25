from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.sandbox_service import SandboxService
from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.base import DomainModel
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.sandbox import SandboxFailure, SandboxRunSpec
from thoth.ports.action import ActionStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ExecutionListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    plan_state: str | None = Field(default=None, max_length=80)


class ExecutionReadInput(ProjectInput):
    plan_execution_id: str = Field(min_length=1, max_length=160)


class StepInput(ExecutionReadInput):
    step_id: str = Field(min_length=1, max_length=160)


class AttemptListInput(ExecutionReadInput):
    step_id: str | None = Field(default=None, max_length=160)
    attempt_state: str | None = Field(default=None, max_length=80)


class AttemptReadInput(ProjectInput):
    attempt_id: str = Field(min_length=1, max_length=160)


class ReconciliationReadInput(ProjectInput):
    reconciliation_id: str = Field(min_length=1, max_length=160)


class StartInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    plan_revision_digest: str = Field(min_length=64, max_length=64)
    execution_profile_ref: str = Field(min_length=1, max_length=160)
    expected_working_head_digest: str = Field(min_length=64, max_length=64)


class RevisionBoundInput(ExecutionReadInput):
    expected_execution_revision: int = Field(ge=0)


class PauseInput(RevisionBoundInput):
    reason: str = Field(min_length=1, max_length=2_000)


class ResumeInput(RevisionBoundInput):
    checkpoint_digest: str = Field(min_length=64, max_length=64)


class CancelInput(RevisionBoundInput):
    scope: str = Field(min_length=1, max_length=260)
    reason: str = Field(min_length=1, max_length=2_000)


class RetryInput(RevisionBoundInput):
    step_id: str = Field(min_length=1, max_length=160)
    failed_attempt_id: str = Field(min_length=1, max_length=160)
    retry_reason: str = Field(min_length=1, max_length=2_000)


class ReconcileInput(ExecutionReadInput):
    attempt_id: str = Field(min_length=1, max_length=160)
    target_state_evidence_refs: tuple[str, ...]
    reconciliation_method: str = Field(min_length=1, max_length=160)


class ObservationLinkInput(ProjectInput):
    attempt_id: str = Field(min_length=1, max_length=160)
    observation_refs: tuple[str, ...]
    completeness: str = Field(pattern=r"^(COMPLETE|PARTIAL|MISSING|CORRUPT|NOT_APPLICABLE)$")
    evidence_refs: tuple[str, ...]
    expected_execution_revision: int = Field(ge=0)


class CompensationInput(ProjectInput):
    attempt_id: str = Field(min_length=1, max_length=160)
    effect_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)


class InvalidateInput(ExecutionReadInput):
    cause_revision_ref: str = Field(min_length=1, max_length=260)
    impact_refs: tuple[str, ...]


class ExecutionHandlers:
    def __init__(
        self,
        *,
        store: ExecutionStorePort,
        actions: ActionStorePort,
        service: ExecutionService,
        sandbox: SandboxService | None = None,
    ) -> None:
        self._store = store
        self._actions = actions
        self._service = service
        self._sandbox = sandbox

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExecutionListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_executions(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
            and (request.plan_id is None or item.plan_id == request.plan_id)
            and (request.plan_state is None or item.state == request.plan_state)
        )
        return {
            "executions": [
                {
                    "plan_execution_id": item.plan_execution_id,
                    "object_id": item.object_id,
                    "plan_id": item.plan_id,
                    "state": item.state,
                    "frontier": list(item.executable_steps),
                    "protected_boundary": list(item.protected_steps),
                    "attempt_count": len(item.attempt_refs),
                    "revision": item.revision,
                    "revision_digest": item.revision_digest,
                }
                for item in items
            ],
            "next_cursor": None,
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExecutionReadInput.model_validate(value)
        item = self._read_execution(request)
        return {
            "execution": item.model_dump(mode="json"),
            "attempts": [
                attempt.model_dump(mode="json")
                for attempt in self._store.list_attempts(
                    request.project_id, request.plan_execution_id
                )
            ],
        }

    async def frontier_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExecutionReadInput.model_validate(value)
        item = self._read_execution(request)
        return cast(
            dict[str, JsonValue],
            {
                "executable": list(item.executable_steps),
                "blocked": item.blocked_steps,
                "protected": list(item.protected_steps),
                "prohibited": list(item.prohibited_steps),
                "completed": list(item.completed_steps),
                "plan_revision_digest": item.plan_revision_digest,
                "checkpoint_digest": item.checkpoint_digest,
            },
        )

    async def preflight_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StepInput.model_validate(value)
        execution = self._read_execution(request)
        plan = self._plan(execution)
        step = self._step(plan, request.step_id)
        attempts = tuple(
            item
            for item in self._store.list_attempts(request.project_id, request.plan_execution_id)
            if item.step_id == request.step_id
        )
        permitted = (
            "DISPATCH"
            if request.step_id in execution.executable_steps and step.get("risk_tier") != "R4"
            else "PROTECTED_BOUNDARY"
            if request.step_id in execution.protected_steps
            else "PROHIBITED"
            if request.step_id in execution.prohibited_steps
            else "WAIT"
        )
        return cast(
            dict[str, JsonValue],
            {
                "checks": {
                    "dependency": execution.blocked_steps.get(request.step_id, "PASS"),
                    "input_target": "BOUND_AT_ATTEMPT",
                    "policy": step.get("policy_state"),
                    "authorization": (
                        "REQUIRED" if step.get("risk_tier") == "R3" else "NOT_REQUIRED"
                    ),
                    "idempotency": (
                        attempts[-1].delivery_guarantee if attempts else "NOT_DISPATCHED"
                    ),
                    "observability": ("PASS" if step.get("output_contract") else "MISSING"),
                },
                "permitted_transition": permitted,
            },
        )

    async def attempt_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttemptListInput.model_validate(value)
        self._read_execution(request)
        items = tuple(
            item
            for item in self._store.list_attempts(request.project_id, request.plan_execution_id)
            if (request.step_id is None or item.step_id == request.step_id)
            and (request.attempt_state is None or item.state == request.attempt_state)
        )
        return {"attempts": [item.model_dump(mode="json") for item in items]}

    async def attempt_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttemptReadInput.model_validate(value)
        return {"attempt": self._read_attempt(request).model_dump(mode="json")}

    async def effect_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttemptReadInput.model_validate(value)
        attempt = self._read_attempt(request)
        return {
            "attempt_state": attempt.state,
            "effect_state": attempt.effect_state,
            "effects": [
                item.model_dump(mode="json")
                for item in self._store.list_effects(request.project_id, request.attempt_id)
            ],
        }

    async def reconciliation_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReconciliationReadInput.model_validate(value)
        item = self._store.read_reconciliation(request.project_id, request.reconciliation_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "reconciliation not found")
        return {"reconciliation": item.model_dump(mode="json")}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExecutionReadInput.model_validate(value)
        self._read_execution(request)
        return {
            "records": [
                item.model_dump(mode="json")
                for item in self._store.list_audit(request.project_id, request.plan_execution_id)
            ]
        }

    async def start(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StartInput.model_validate(value)
        plan = self._actions.read_plan(
            request.project_id, request.plan_id, request.plan_revision_digest
        )
        if plan is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "plan revision not found")
        try:
            if self._sandbox is not None:
                for step in plan.steps:
                    sandbox_value = step.get("sandbox_spec")
                    if step.get("risk_tier") != "R2" or not isinstance(sandbox_value, dict):
                        continue
                    preflight_spec = self._sandbox_spec(
                        request.project_id,
                        f"preflight:{step.get('step_id', 'unknown')}",
                        cast(dict[str, object], sandbox_value),
                    )
                    self._sandbox.preflight(preflight_spec)
            execution, attempts, commit = self._service.start(
                project_id=request.project_id,
                plan=plan,
                execution_profile_ref=request.execution_profile_ref,
                expected_working_head_digest=request.expected_working_head_digest,
            )
            sandbox_runs: list[dict[str, JsonValue]] = []
            if self._sandbox is not None:
                pending = list(attempts)
                while pending:
                    attempt = pending.pop(0)
                    step = self._step(plan, attempt.step_id)
                    sandbox_value = step.get("sandbox_spec")
                    if step.get("risk_tier") != "R2" or not isinstance(sandbox_value, dict):
                        continue
                    sandbox_spec = self._sandbox_spec(
                        request.project_id,
                        attempt.attempt_id,
                        cast(dict[str, object], sandbox_value),
                    )
                    bundle = await self._sandbox.run(sandbox_spec)
                    observation_refs = tuple(
                        item.span_id for item in bundle.observation.evidence_candidates
                    )
                    with self._sandbox.accepting_observation(
                        sandbox_spec, bundle
                    ) as admitted_artifact:
                        revised_attempt, execution, new_attempts, commit = (
                            self._service.complete_sandbox_attempt(
                                execution,
                                plan,
                                attempt,
                                result=bundle.result,
                                receipt=bundle.receipt,
                                observation_refs=observation_refs,
                            )
                        )
                    pending.extend(new_attempts)
                    sandbox_runs.append(
                        cast(
                            dict[str, JsonValue],
                            {
                                "attempt": revised_attempt.model_dump(mode="json"),
                                "result": bundle.result.model_dump(mode="json"),
                                "receipt": bundle.receipt.model_dump(mode="json"),
                                "observation_artifact": (admitted_artifact.model_dump(mode="json")),
                                "observation_refs": list(observation_refs),
                            },
                        )
                    )
        except SandboxFailure as exc:
            data: dict[str, JsonValue] = {"sandbox_error": exc.code.value}
            if exc.policy_denial is not None:
                data["policy_denial"] = cast(
                    JsonValue,
                    exc.policy_denial.model_dump(mode="json"),
                )
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                str(exc),
                data=data,
            ) from exc
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "execution": execution.model_dump(mode="json"),
                "initial_preflight_frontier": list(execution.executable_steps),
                "attempts": [item.model_dump(mode="json") for item in attempts],
                "sandbox_runs": sandbox_runs,
                "checkpoint": execution.checkpoint_digest,
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PauseInput.model_validate(value)
        current = self._read_expected(request)
        try:
            execution, commit = self._service.pause(current, request.reason)
        except ValueError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                str(exc),
                data={"reason_code": "EXECUTION_STATE_CONFLICT"},
            ) from exc
        return {
            "execution": execution.model_dump(mode="json"),
            "checkpoint": execution.checkpoint_digest,
            "running_external_effects_cancelled": False,
            "commit": commit.model_dump(mode="json"),
        }

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ResumeInput.model_validate(value)
        current = self._read_expected(request)
        try:
            execution, attempts, commit = self._service.resume(
                current, self._plan(current), request.checkpoint_digest
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "execution": execution.model_dump(mode="json"),
            "frontier": list(execution.executable_steps),
            "new_attempts": [item.model_dump(mode="json") for item in attempts],
            "commit": commit.model_dump(mode="json"),
        }

    async def cancel(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CancelInput.model_validate(value)
        current = self._read_expected(request)
        try:
            execution, attempts, commit = self._service.cancel(
                current, scope=request.scope, reason=request.reason
            )
        except ValueError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                str(exc),
                data={"reason_code": "EXECUTION_STATE_CONFLICT"},
            ) from exc
        cancel_results: dict[str, bool] = {}
        if self._sandbox is not None:
            for attempt in attempts:
                cancel_results[attempt.attempt_id] = await self._sandbox.cancel(attempt.attempt_id)
        return cast(
            dict[str, JsonValue],
            {
                "execution": execution.model_dump(mode="json"),
                "affected_attempts": [item.model_dump(mode="json") for item in attempts],
                "cancel_dispatch_plan": [item.attempt_id for item in attempts],
                "sandbox_cancel_results": cancel_results,
                "effect_may_continue_warnings": [
                    item.attempt_id for item in attempts if item.effect_state != "NONE"
                ],
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def retry(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RetryInput.model_validate(value)
        current = self._read_expected(request)
        try:
            execution, attempt, commit = self._service.retry(
                current,
                self._plan(current),
                step_id=request.step_id,
                failed_attempt_id=request.failed_attempt_id,
                retry_reason=request.retry_reason,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "execution": execution.model_dump(mode="json"),
            "attempt": attempt.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    async def reconcile(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReconcileInput.model_validate(value)
        current = self._read_execution(request)
        attempt = self._store.read_attempt(request.project_id, request.attempt_id)
        if attempt is None or attempt.plan_execution_id != request.plan_execution_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "attempt not found")
        try:
            reconciliation, revised_attempt, effect, execution, attempts, commit = (
                self._service.reconcile(
                    current,
                    self._plan(current),
                    attempt,
                    evidence_refs=request.target_state_evidence_refs,
                    method=request.reconciliation_method,
                )
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "reconciliation": reconciliation.model_dump(mode="json"),
            "attempt": revised_attempt.model_dump(mode="json"),
            "effect": effect.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json"),
            "new_attempts": [item.model_dump(mode="json") for item in attempts],
            "retry_permission": reconciliation.retry_permitted,
            "commit": commit.model_dump(mode="json"),
        }

    async def observation_link(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObservationLinkInput.model_validate(value)
        attempt = self._read_attempt(
            AttemptReadInput(project_id=request.project_id, attempt_id=request.attempt_id)
        )
        current = self._store.read_execution(request.project_id, attempt.plan_execution_id)
        if current is None or current.revision != request.expected_execution_revision:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Execution revision changed")
        try:
            revised_attempt, execution, commit = self._service.link_observation(
                current,
                attempt,
                observation_refs=request.observation_refs,
                completeness=request.completeness,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "attempt": revised_attempt.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json"),
            "downstream_outcome_readiness": (
                "READY" if request.completeness == "COMPLETE" else "NOT_READY"
            ),
            "commit": commit.model_dump(mode="json"),
        }

    async def compensation_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CompensationInput.model_validate(value)
        attempt = self._read_attempt(
            AttemptReadInput(project_id=request.project_id, attempt_id=request.attempt_id)
        )
        current = self._store.read_execution(request.project_id, attempt.plan_execution_id)
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Execution not found")
        try:
            action, execution, action_commit, execution_commit = self._service.propose_compensation(
                current,
                attempt,
                effect_refs=request.effect_refs,
                reason=request.reason,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "compensation_action_ref": action.action_id,
            "action": action.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json"),
            "residual_effect_context": "REQUIRES_OBSERVATION",
            "commits": [
                action_commit.model_dump(mode="json"),
                execution_commit.model_dump(mode="json"),
            ],
        }

    async def invalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvalidateInput.model_validate(value)
        current = self._read_execution(request)
        execution, commit = self._service.invalidate(
            current,
            cause_revision_ref=request.cause_revision_ref,
            impact_refs=request.impact_refs,
        )
        return {
            "execution": execution.model_dump(mode="json"),
            "running_effect_warning": execution.blocked_steps.get("INVALIDATED"),
            "downstream_stale_set": list(request.impact_refs),
            "commit": commit.model_dump(mode="json"),
        }

    def _read_execution(self, request: ExecutionReadInput) -> PlanExecutionRecord:
        item = self._store.read_execution(request.project_id, request.plan_execution_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Execution not found")
        return item

    def _read_expected(self, request: RevisionBoundInput) -> PlanExecutionRecord:
        item = self._read_execution(request)
        if item.revision != request.expected_execution_revision:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Execution revision changed")
        return item

    def _read_attempt(self, request: AttemptReadInput) -> StepExecutionAttemptRecord:
        item = self._store.read_attempt(request.project_id, request.attempt_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "attempt not found")
        return item

    def _plan(self, execution: PlanExecutionRecord) -> ActionPlanRecord:
        item = self._actions.read_plan(
            execution.project_id, execution.plan_id, execution.plan_revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "ActionPlan not found")
        return item

    @staticmethod
    def _step(plan: ActionPlanRecord, step_id: str) -> dict[str, object]:
        for step in plan.steps:
            if step.get("step_id") == step_id:
                return step
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "step not found")

    @staticmethod
    def _sandbox_spec(
        project_id: str,
        attempt_id: str,
        value: dict[str, object],
    ) -> SandboxRunSpec:
        return SandboxRunSpec.model_validate(
            {
                **value,
                "project_id": project_id,
                "attempt_id": attempt_id,
            }
        )
