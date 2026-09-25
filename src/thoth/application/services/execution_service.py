from __future__ import annotations

from typing import cast

from thoth.application.services.action_currentness import find_plan_step, require_plan_current
from thoth.application.services.action_service import ActionService
from thoth.application.services.execution_frontier import execution_frontier
from thoth.application.services.execution_persistence import (
    append_execution_audit,
    persist_execution,
)
from thoth.application.services.execution_preparation import (
    execution_checkpoint,
    execution_digest,
    execution_resume_token,
    prepare_execution_record,
    sandbox_attempt_updates,
)
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.action_full import ActionPlanRecord, AuthorizationEnvelopeRecord
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import CutoffState
from thoth.domain.execution_full import (
    ExecutionAuditRecord,
    ExecutionEffectRecord,
    PlanExecutionRecord,
    ReconciliationRecord,
    StepExecutionAttemptRecord,
)
from thoth.domain.sandbox import SandboxReceipt, SandboxResult
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class ExecutionService:
    def __init__(
        self,
        *,
        store: ExecutionStorePort,
        actions: ActionStorePort,
        action_service: ActionService,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._actions = actions
        self._action_service = action_service
        self._artifacts = artifacts
        self._ledger = ledger
        self._commits = commits
        self._clock = clock
        self._ids = ids

    def start(
        self,
        *,
        project_id: str,
        plan: ActionPlanRecord,
        execution_profile_ref: str,
        expected_working_head_digest: str,
        selected_step_ids: tuple[str, ...] = (),
    ) -> tuple[PlanExecutionRecord, tuple[StepExecutionAttemptRecord, ...], CommitResult]:
        with self._ledger.transaction():
            require_plan_current(self._ledger, self._actions, plan)
            current_head = head_set_digest(self._ledger.read_heads(project_id))
            if current_head != expected_working_head_digest:
                raise ValueError("working head changed before Execution start")
            frontier = self._frontier(plan, (), selected_step_ids)
            execution = prepare_execution_record(
                project_id=project_id,
                plan=plan,
                execution_profile_ref=execution_profile_ref,
                expected_working_head_digest=expected_working_head_digest,
                selected_step_ids=selected_step_ids,
                frontier=frontier,
                clock=self._clock,
                ids=self._ids,
            )
            attempts = tuple(
                self._create_attempt(execution, plan, step_id)
                for step_id in execution.executable_steps
            )
            execution = execution.model_copy(
                update={"attempt_refs": tuple(item.attempt_id for item in attempts)}
            )
            execution = execution.model_copy(
                update={
                    "revision_digest": execution_digest(execution),
                }
            )
            committed, commit = self._persist_execution(execution, "execution/started")
            for attempt in attempts:
                self._store.add_attempt(attempt)
                self.audit(
                    project_id,
                    execution.plan_execution_id,
                    "execution/attemptStarted",
                    {"attempt_id": attempt.attempt_id, "step_id": attempt.step_id},
                )
            return committed, attempts, commit

    def revise_execution(
        self,
        current: PlanExecutionRecord,
        *,
        updates: dict[str, object],
        event_type: str,
    ) -> tuple[PlanExecutionRecord, CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            draft = current.model_dump(mode="python")
            draft.update(updates)
            draft.update(
                {
                    "execution_revision_id": self._ids.new("execution-revision"),
                    "revision": current.revision + 1,
                    "supersedes_revision_digest": current.revision_digest,
                    "updated_at": self._clock.now(),
                }
            )
            draft.pop("revision_digest", None)
            checkpoint = execution_checkpoint(
                current.plan_execution_id,
                current.plan_revision_digest,
                current.revision + 1,
                tuple(cast(tuple[str, ...], draft.get("executable_steps", ()))),
                cast(dict[str, str], draft.get("blocked_steps", {})),
                tuple(cast(tuple[str, ...], draft.get("protected_steps", ()))),
                current.selected_step_ids,
            )
            draft["checkpoint_digest"] = checkpoint
            draft["resume_token"] = execution_resume_token(checkpoint, current.plan_execution_id)
            revised = PlanExecutionRecord.model_validate(
                {**draft, "revision_digest": self._digest("PLAN_EXECUTION", draft)}
            )
            return self._persist_execution(revised, event_type)

    def pause(
        self, current: PlanExecutionRecord, reason: str
    ) -> tuple[PlanExecutionRecord, CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            return self.revise_execution(
                current,
                updates={
                    "state": "PAUSED_FRONTIER",
                    "blocked_steps": {**current.blocked_steps, "PAUSE": reason},
                },
                event_type="execution/paused",
            )

    def resume(
        self,
        current: PlanExecutionRecord,
        plan: ActionPlanRecord,
        checkpoint_digest: str,
    ) -> tuple[PlanExecutionRecord, tuple[StepExecutionAttemptRecord, ...], CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            require_plan_current(self._ledger, self._actions, plan)
            if any(
                attempt.observation_completeness == "NOT_ADMITTED"
                for attempt in self._store.list_attempts(
                    current.project_id, current.plan_execution_id
                )
            ):
                raise ValueError("EXECUTION_OBSERVATION_RECONCILIATION_REQUIRED")
            if current.checkpoint_digest != checkpoint_digest:
                raise ValueError("Execution checkpoint digest mismatch")
            if current.state not in {
                "PAUSED_FRONTIER",
                "PAUSED_PROTECTED_BOUNDARY",
                "PAUSED_POLICY",
                "PARTIAL",
            }:
                raise ValueError("Execution is not resumable")
            return self._advance(current, plan, "execution/resumed")

    def cancel(
        self, current: PlanExecutionRecord, *, scope: str, reason: str
    ) -> tuple[PlanExecutionRecord, tuple[StepExecutionAttemptRecord, ...], CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            affected: list[StepExecutionAttemptRecord] = []
            for attempt in self._store.list_attempts(current.project_id, current.plan_execution_id):
                if attempt.state not in {"DISPATCHED", "RUNNING", "UNKNOWN_COMPLETION"}:
                    continue
                revised = self._revise_attempt(
                    attempt,
                    updates={
                        "state": "CANCEL_REQUESTED",
                        "effect_state": (
                            "EFFECT_MAY_CONTINUE" if attempt.effect_state != "NONE" else "POSSIBLE"
                        ),
                    },
                )
                affected.append(revised)
            execution, commit = self.revise_execution(
                current,
                updates={"state": "CANCEL_REQUESTED"},
                event_type="execution/cancelRequested",
            )
            self.audit(
                current.project_id,
                current.plan_execution_id,
                "execution/cancelRequested",
                {"scope": scope, "reason": reason},
            )
            return execution, tuple(affected), commit

    def retry(
        self,
        current: PlanExecutionRecord,
        plan: ActionPlanRecord,
        *,
        step_id: str,
        failed_attempt_id: str,
        retry_reason: str,
    ) -> tuple[PlanExecutionRecord, StepExecutionAttemptRecord, CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            failed = self._store.read_attempt(current.project_id, failed_attempt_id)
            if failed is None or failed.plan_execution_id != current.plan_execution_id:
                raise ValueError("failed attempt not found")
            if failed.step_id != step_id or failed.state != "FAILED":
                raise ValueError("retry requires a FAILED attempt for the same step")
            if failed.failure_class != "TRANSIENT" or failed.delivery_guarantee not in {
                "IDEMPOTENT_RETRYABLE",
                "AT_LEAST_ONCE",
            }:
                raise ValueError(
                    "failure/delivery policy requires reconciliation or a new revision"
                )
            prior = tuple(
                item
                for item in self._store.list_attempts(current.project_id, current.plan_execution_id)
                if item.step_id == step_id
            )
            if len(prior) >= 3:
                raise ValueError("bounded retry limit exhausted")
            attempt = self._create_attempt(
                current,
                plan,
                step_id,
                attempt_number=max(item.attempt_number for item in prior) + 1,
            )
            self._store.add_attempt(attempt)
            execution, commit = self.revise_execution(
                current,
                updates={
                    "state": "RUNNING",
                    "attempt_refs": (*current.attempt_refs, attempt.attempt_id),
                },
                event_type="execution/attemptStarted",
            )
            self.audit(
                current.project_id,
                current.plan_execution_id,
                "execution/attemptStarted",
                {"retry_reason": retry_reason, "attempt_id": attempt.attempt_id},
            )
            return execution, attempt, commit

    def reconcile(
        self,
        current: PlanExecutionRecord,
        plan: ActionPlanRecord,
        attempt: StepExecutionAttemptRecord,
        *,
        evidence_refs: tuple[str, ...],
        method: str,
    ) -> tuple[
        ReconciliationRecord,
        StepExecutionAttemptRecord,
        ExecutionEffectRecord,
        PlanExecutionRecord,
        tuple[StepExecutionAttemptRecord, ...],
        CommitResult,
    ]:
        with self._ledger.transaction():
            self._assert_current(current)
            self._validate_evidence(current.project_id, evidence_refs)
            findings = {
                "TARGET_CHECK_CONFIRMED_NOT_APPLIED": (
                    "CONFIRMED_NOT_APPLIED",
                    "NONE",
                    True,
                    "RETRY_ALLOWED",
                ),
                "TARGET_CHECK_CONFIRMED_APPLIED": (
                    "CONFIRMED_APPLIED",
                    "CONFIRMED",
                    False,
                    "LINK_OBSERVATION",
                ),
                "TARGET_CHECK_PARTIAL": (
                    "PARTIAL",
                    "PARTIAL",
                    False,
                    "COMPENSATION_OR_NEW_REVISION",
                ),
                "TARGET_CHECK_STILL_UNKNOWN": (
                    "STILL_UNKNOWN",
                    "EFFECT_MAY_CONTINUE",
                    False,
                    "RECONCILE_AGAIN",
                ),
            }
            if method not in findings:
                raise ValueError("unsupported reconciliation method")
            finding, effect_state, retry, next_action = findings[method]
            state = {
                "CONFIRMED_NOT_APPLIED": "FAILED",
                "CONFIRMED_APPLIED": "SUCCEEDED",
                "PARTIAL": "PARTIAL",
                "STILL_UNKNOWN": "UNKNOWN_COMPLETION",
            }[finding]
            revised_attempt = self._revise_attempt(
                attempt,
                updates={
                    "state": state,
                    "failure_class": "TRANSIENT" if retry else None,
                    "effect_state": effect_state,
                    "completed_at": (
                        self._clock.now() if state in {"FAILED", "SUCCEEDED", "PARTIAL"} else None
                    ),
                    "output_refs": evidence_refs if state == "SUCCEEDED" else attempt.output_refs,
                    "output_digests": tuple(
                        span.text_sha256
                        for reference in evidence_refs
                        if (span := self._artifacts.read_evidence(reference)) is not None
                    ),
                },
            )
            reconciliation_draft: dict[str, object] = {
                "reconciliation_id": self._ids.new("reconciliation"),
                "project_id": current.project_id,
                "plan_execution_id": current.plan_execution_id,
                "attempt_id": attempt.attempt_id,
                "method": method,
                "target_state_evidence_refs": evidence_refs,
                "finding": finding,
                "duplicate_risk": (
                    "NONE_CONFIRMED" if finding == "CONFIRMED_NOT_APPLIED" else "POSSIBLE"
                ),
                "retry_permitted": retry,
                "next_permitted_action": next_action,
                "created_at": self._clock.now(),
            }
            reconciliation = ReconciliationRecord.model_validate(
                {
                    **reconciliation_draft,
                    "reconciliation_digest": self._digest(
                        "EXECUTION_RECONCILIATION", reconciliation_draft
                    ),
                }
            )
            self._store.add_reconciliation(reconciliation)
            effect_draft: dict[str, object] = {
                "effect_id": self._ids.new("execution-effect"),
                "project_id": current.project_id,
                "attempt_id": attempt.attempt_id,
                "effect_state": effect_state,
                "effect_refs": evidence_refs,
                "target_reconciliation_required": finding == "STILL_UNKNOWN",
                "created_at": self._clock.now(),
            }
            effect = ExecutionEffectRecord.model_validate(
                {
                    **effect_draft,
                    "effect_digest": self._digest("EXECUTION_EFFECT", effect_draft),
                }
            )
            self._store.add_effect(effect)
            completed = (
                (*current.completed_steps, attempt.step_id)
                if state == "SUCCEEDED" and attempt.step_id not in current.completed_steps
                else current.completed_steps
            )
            base, commit = self.revise_execution(
                current,
                updates={
                    "completed_steps": completed,
                    "effect_refs": (*current.effect_refs, effect.effect_id),
                    "reconciliation_refs": (
                        *current.reconciliation_refs,
                        reconciliation.reconciliation_id,
                    ),
                    "state": (
                        "PARTIAL" if state in {"PARTIAL", "UNKNOWN_COMPLETION"} else current.state
                    ),
                },
                event_type="execution/reconciliationCompleted",
            )
            advanced, new_attempts, advance_commit = self._advance(
                base, plan, "execution/frontierChanged"
            )
            return (
                reconciliation,
                revised_attempt,
                effect,
                advanced,
                new_attempts,
                advance_commit if new_attempts or advanced.revision != base.revision else commit,
            )

    def link_observation(
        self,
        current: PlanExecutionRecord,
        attempt: StepExecutionAttemptRecord,
        *,
        observation_refs: tuple[str, ...],
        completeness: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[StepExecutionAttemptRecord, PlanExecutionRecord, CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            self._validate_evidence(current.project_id, (*observation_refs, *evidence_refs))
            revised_attempt = self._revise_attempt(
                attempt,
                updates={
                    "observation_refs": observation_refs,
                    "observation_completeness": completeness,
                },
            )
            execution, commit = self.revise_execution(
                current,
                updates={
                    "observation_refs": tuple(
                        dict.fromkeys((*current.observation_refs, *observation_refs))
                    )
                },
                event_type="execution/effectChanged",
            )
            return revised_attempt, execution, commit

    def complete_sandbox_attempt(
        self,
        current: PlanExecutionRecord,
        plan: ActionPlanRecord,
        attempt: StepExecutionAttemptRecord,
        *,
        result: SandboxResult,
        receipt: SandboxReceipt,
        observation_refs: tuple[str, ...],
        observation_admitted: bool = True,
        admission_reason: str = "RESULT_NOT_ADMITTED",
    ) -> tuple[
        StepExecutionAttemptRecord,
        PlanExecutionRecord,
        tuple[StepExecutionAttemptRecord, ...],
        CommitResult,
    ]:
        with self._ledger.transaction():
            self._assert_current(current)
            if attempt.project_id != result.project_id or attempt.attempt_id != result.attempt_id:
                raise ValueError("sandbox result is not bound to this execution attempt")
            if attempt.state not in {"DISPATCHED", "RUNNING"}:
                raise ValueError("sandbox completion requires an active attempt")
            revised_attempt = self._revise_attempt(
                attempt,
                updates=sandbox_attempt_updates(
                    result,
                    receipt,
                    observation_refs,
                    self._artifacts,
                    observation_admitted=observation_admitted,
                ),
            )
            if not observation_admitted:
                execution, commit = self.revise_execution(
                    current,
                    updates={
                        "state": "PARTIAL",
                        "blocked_steps": {
                            **current.blocked_steps,
                            attempt.step_id: admission_reason,
                        },
                        "observation_refs": tuple(
                            dict.fromkeys((*current.observation_refs, *observation_refs))
                        ),
                    },
                    event_type="execution/resultNotAdmitted",
                )
                return revised_attempt, execution, (), commit
            if revised_attempt.state != "SUCCEEDED":
                execution, commit = self.revise_execution(
                    current,
                    updates={
                        "state": "PARTIAL",
                        "blocked_steps": {
                            **current.blocked_steps,
                            attempt.step_id: result.state.value,
                        },
                    },
                    event_type="execution/sandboxAttemptFailed",
                )
                return revised_attempt, execution, (), commit
            base, commit = self.revise_execution(
                current,
                updates={
                    "completed_steps": tuple(
                        dict.fromkeys((*current.completed_steps, attempt.step_id))
                    ),
                    "effect_refs": tuple(
                        dict.fromkeys((*current.effect_refs, receipt.receipt_digest))
                    ),
                    "observation_refs": tuple(
                        dict.fromkeys((*current.observation_refs, *observation_refs))
                    ),
                },
                event_type="execution/sandboxAttemptSucceeded",
            )
            advanced, attempts, advance_commit = self._advance(
                base, plan, "execution/frontierChanged"
            )
            return (
                revised_attempt,
                advanced,
                attempts,
                advance_commit if attempts or advanced.revision != base.revision else commit,
            )

    def invalidate(
        self,
        current: PlanExecutionRecord,
        *,
        cause_revision_ref: str,
        impact_refs: tuple[str, ...],
    ) -> tuple[PlanExecutionRecord, CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            running_effect = any(
                item.state in {"DISPATCHED", "RUNNING", "CANCEL_REQUESTED", "UNKNOWN_COMPLETION"}
                and item.effect_state != "NONE"
                for item in self._store.list_attempts(current.project_id, current.plan_execution_id)
            )
            return self.revise_execution(
                current,
                updates={
                    "state": "INVALIDATED",
                    "cause_revision_ref": cause_revision_ref,
                    "invalidation_impact_refs": impact_refs,
                    "blocked_steps": {
                        **current.blocked_steps,
                        "INVALIDATED": (
                            "RUNNING_EFFECT_MAY_CONTINUE" if running_effect else "NO_RUNNING_EFFECT"
                        ),
                    },
                },
                event_type="execution/invalidated",
            )

    def propose_compensation(
        self,
        current: PlanExecutionRecord,
        attempt: StepExecutionAttemptRecord,
        *,
        effect_refs: tuple[str, ...],
        reason: str,
    ):
        with self._ledger.transaction():
            self._assert_current(current)
            plan = self._actions.read_plan(current.project_id, current.plan_id, None)
            if plan is None:
                raise ValueError("ActionPlan was not found for compensation")
            specification: dict[str, object] = {
                "description": reason,
                "caused_by_execution_attempt_ref": attempt.attempt_id,
                "observed_effect_refs": effect_refs,
                "expected_observation_or_change": {
                    "description": "residual effect is reduced and remains observable"
                },
                "effect_completeness_confirmed": False,
                "effect_vector": {"effect_completeness_confirmed": False},
                "stop_conditions": ("residual effect boundary reached",),
                "observability": "effect and residual evidence required",
            }
            action, _duplicates, commit = self._action_service.create(
                project_id=current.project_id,
                object_id=current.object_id,
                portfolio_id=f"action-portfolio:{current.object_id}",
                hypothesis_refs=(),
                primary_purpose="RESTORE_COMPENSATE",
                secondary_purposes=("RISK_REDUCTION",),
                specification=specification,
                evidence_refs=(),
            )
            execution, execution_commit = self.revise_execution(
                current,
                updates={
                    "state": "COMPENSATION_PENDING",
                    "compensation_refs": (*current.compensation_refs, action.action_id),
                },
                event_type="execution/compensationRequired",
            )
            return action, execution, commit, execution_commit

    def audit(
        self,
        project_id: str,
        execution_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> ExecutionAuditRecord:
        return append_execution_audit(
            project_id,
            execution_id,
            event_type,
            payload,
            store=self._store,
            clock=self._clock,
            ids=self._ids,
        )

    def _advance(
        self, current: PlanExecutionRecord, plan: ActionPlanRecord, event_type: str
    ) -> tuple[PlanExecutionRecord, tuple[StepExecutionAttemptRecord, ...], CommitResult]:
        with self._ledger.transaction():
            self._assert_current(current)
            executable, blocked, protected, prohibited = self._frontier(
                plan, current.completed_steps, current.selected_step_ids
            )
            attempted_steps = {
                attempt.step_id
                for attempt in self._store.list_attempts(
                    current.project_id, current.plan_execution_id
                )
                if attempt.state not in {"FAILED", "INVALIDATED", "CANCELLED_CONFIRMED"}
            }
            dispatch = tuple(step for step in executable if step not in attempted_steps)
            attempts = tuple(self._create_attempt(current, plan, step_id) for step_id in dispatch)
            for attempt in attempts:
                self._store.add_attempt(attempt)
            all_steps = set(current.selected_step_ids) or {
                str(step["step_id"]) for step in plan.steps
            }
            completed = set(current.completed_steps)
            state = (
                "COMPLETED"
                if completed == all_steps
                else "RUNNING"
                if executable
                else "PAUSED_PROTECTED_BOUNDARY"
                if protected
                else "PARTIAL"
            )
            execution, commit = self.revise_execution(
                current,
                updates={
                    "state": state,
                    "executable_steps": executable,
                    "blocked_steps": blocked,
                    "protected_steps": protected,
                    "prohibited_steps": prohibited,
                    "attempt_refs": (
                        *current.attempt_refs,
                        *(item.attempt_id for item in attempts),
                    ),
                },
                event_type=event_type,
            )
            return execution, attempts, commit

    def _frontier(
        self,
        plan: ActionPlanRecord,
        completed_steps: tuple[str, ...],
        selected_step_ids: tuple[str, ...] = (),
    ) -> tuple[tuple[str, ...], dict[str, str], tuple[str, ...], tuple[str, ...]]:
        return execution_frontier(
            plan, completed_steps, selected_step_ids, self._actions, self._ledger, self._clock
        )

    def _create_attempt(
        self,
        execution: PlanExecutionRecord,
        plan: ActionPlanRecord,
        step_id: str,
        *,
        attempt_number: int = 1,
    ) -> StepExecutionAttemptRecord:
        require_plan_current(self._ledger, self._actions, plan)
        step = self._step(plan, step_id)
        risk = str(step.get("risk_tier", "R3"))
        authorization: AuthorizationEnvelopeRecord | None = None
        if risk == "R3":
            authorization = next(
                (
                    item
                    for item in self._actions.list_authorizations(plan.project_id, plan.plan_id)
                    if item.step_id == step_id
                    and item.plan_revision_digest == plan.revision_digest
                    and item.state == "APPROVED"
                ),
                None,
            )
            if authorization is None:
                raise ValueError("R3 dispatch requires an approved exact-digest authorization")
            authorization = self._action_service.consume_authorization(
                authorization, exact_scope_digest=authorization.exact_scope_digest
            )
        input_values = self._strings(step.get("input_digests", step.get("inputs", ())))
        input_digests = tuple(
            value
            if len(value) == 64
            else domain_digest("EXECUTION_INPUT", "1.0.0", canonical_payload({"value": value}))
            for value in input_values
        )
        target_values = self._strings(step.get("target_digests", ()))
        target_digests = tuple(
            value
            if len(value) == 64
            else domain_digest("EXECUTION_TARGET", "1.0.0", canonical_payload({"value": value}))
            for value in target_values
        )
        environment = {
            "execution_profile_ref": execution.execution_profile_ref,
            "risk_tier": risk,
            "sandbox": risk == "R2",
        }
        environment_digest = domain_digest(
            "EXECUTION_ENVIRONMENT", "1.0.0", canonical_payload(environment)
        )
        invocation = {
            "plan_revision_digest": plan.revision_digest,
            "step_id": step_id,
            "input_digests": input_digests,
            "target_digests": target_digests,
            "environment_digest": environment_digest,
            "authorization_digest": (
                None if authorization is None else authorization.exact_scope_digest
            ),
            "attempt_number": attempt_number,
        }
        draft: dict[str, object] = {
            "attempt_revision_id": self._ids.new("attempt-revision"),
            "attempt_id": self._ids.new("execution-attempt"),
            "project_id": execution.project_id,
            "plan_execution_id": execution.plan_execution_id,
            "plan_id": plan.plan_id,
            "plan_revision_digest": plan.revision_digest,
            "step_id": step_id,
            "attempt_number": attempt_number,
            "state": "DISPATCHED",
            "exact_invocation_digest": domain_digest(
                "EXECUTION_INVOCATION", "1.0.0", canonical_payload(invocation)
            ),
            "input_digests": input_digests,
            "target_digests": target_digests,
            "environment_digest": environment_digest,
            "authorization_digest": (
                None if authorization is None else authorization.exact_scope_digest
            ),
            "idempotency_key": f"{execution.plan_execution_id}:{step_id}:{attempt_number}",
            "delivery_guarantee": (
                "IDEMPOTENT_RETRYABLE" if risk in {"R0", "R1", "R2"} else "REQUIRES_RECONCILIATION"
            ),
            "effect_state": "POSSIBLE" if risk == "R3" else "NONE",
            "observation_completeness": "MISSING",
            "heartbeat_at": self._clock.now(),
            "started_at": self._clock.now(),
            "created_at": self._clock.now(),
        }
        return StepExecutionAttemptRecord.model_validate(
            {**draft, "revision_digest": self._digest("STEP_EXECUTION_ATTEMPT", draft)}
        )

    def _revise_attempt(
        self, current: StepExecutionAttemptRecord, *, updates: dict[str, object]
    ) -> StepExecutionAttemptRecord:
        draft = current.model_dump(mode="python")
        draft.update(updates)
        draft.update(
            {
                "attempt_revision_id": self._ids.new("attempt-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        revised = StepExecutionAttemptRecord.model_validate(
            {**draft, "revision_digest": self._digest("STEP_EXECUTION_ATTEMPT", draft)}
        )
        self._store.add_attempt(revised)
        return revised

    def _persist_execution(
        self,
        record: PlanExecutionRecord,
        event_type: str,
    ) -> tuple[PlanExecutionRecord, CommitResult]:
        return persist_execution(
            record,
            event_type,
            store=self._store,
            ledger=self._ledger,
            commits=self._commits,
            clock=self._clock,
            ids=self._ids,
        )

    def _assert_current(self, current: PlanExecutionRecord) -> None:
        latest = self._store.read_execution(current.project_id, current.plan_execution_id)
        if latest is None or latest.revision_digest != current.revision_digest:
            raise ValueError("execution revision changed before mutation")

    def _validate_evidence(self, project_id: str, refs: tuple[str, ...]) -> None:
        for reference in refs:
            span = self._artifacts.read_evidence(reference)
            if span is None or span.project_id != project_id:
                raise ValueError("Execution evidence ref is outside the project")
            if span.cutoff_state != CutoffState.ELIGIBLE:
                raise ValueError("ineligible evidence cannot enter Execution")

    @staticmethod
    def _step(plan: ActionPlanRecord, step_id: str) -> dict[str, object]:
        return find_plan_step(plan, step_id)

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            return ()
        return tuple(str(item) for item in cast(tuple[object, ...] | list[object], value))

    @staticmethod
    def _digest(kind: str, value: dict[str, object]) -> str:
        return domain_digest(kind, "1.0.0", canonical_payload(value))
