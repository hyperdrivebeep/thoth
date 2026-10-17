"""Pure record construction for the R2 closed loop (98).

These functions build the values the coordinator stages, links or reports: the R2 action pick,
a recovery attempt, the linked outcome, the two coordinator actors, failure classification, the
recovery candidate and reconciliation records, and the staged revision of one entity. They read
nothing and write nothing. The coordinator keeps every await, every ledger, sandbox, research and
lifecycle call and every transaction, and passes the id generator and the clock through as the
original objects so ids and times are drawn in the same order as before.
"""

from __future__ import annotations

from typing import cast

from thoth.application.services.sandbox_service import SandboxExecutionBundle
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType, RiskTier
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.policy import R2RecoveryPolicy
from thoth.domain.recovery import (
    ExecutionFailureClass,
    ReconciliationRecord,
    RecoveryAttempt,
    RecoveryCandidateRevision,
)
from thoth.domain.revision import EntitySnapshot, SemanticRevision, StagedRevision
from thoth.domain.sandbox import SandboxExecutionState, SandboxRunSpec
from thoth.domain.test_validity import TestValidityAssessment
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def r2_action(action_plan: ActionPlan) -> ActionCandidate | None:
    by_id = {item.action_id: item for item in action_plan.alternatives}
    action = next(
        (
            by_id[action_id]
            for action_id in action_plan.frontier
            if action_id in by_id and by_id[action_id].risk_tier == RiskTier.R2
        ),
        None,
    )
    return action


def recovery_attempt(
    spec: SandboxRunSpec,
    bundle: SandboxExecutionBundle,
    failure_class: ExecutionFailureClass | None,
    retry_ordinal: int,
) -> RecoveryAttempt:
    return RecoveryAttempt(
        attempt_id=spec.attempt_id,
        input_context_digest=cast(str, spec.input_context_digest),
        result_state=bundle.result.state.value,
        failure_class=failure_class,
        failure_detail=bundle.result.failure_detail,
        retry_ordinal=retry_ordinal,
    )


def assessed_outcome(
    outcome: OutcomeRecord, assessments: tuple[TestValidityAssessment, ...]
) -> OutcomeRecord:
    return outcome.model_copy(
        update={
            "test_validity_assessment_refs": tuple(item.assessment_id for item in assessments),
            "interpretation": "process observation links to scoped test assessments",
            "limitations": (
                "official criterion attainment and causal attribution remain NOT_ASSESSED",
            ),
        }
    )


def closed_loop_actor() -> ActorRef:
    return ActorRef(
        actor_id="agent:r2-closed-loop-coordinator",
        kind=ActorKind.AGENT,
        role="trusted-r2-closed-loop-coordinator",
    )


def recovery_actor() -> ActorRef:
    return ActorRef(
        actor_id="agent:r2-recovery-coordinator",
        kind=ActorKind.AGENT,
        role="bounded-r2-recovery-coordinator",
    )


def classify_failure(
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


def failure_class_of(
    bundle: SandboxExecutionBundle,
    policy: R2RecoveryPolicy,
) -> ExecutionFailureClass | None:
    if bundle.result.state == SandboxExecutionState.SUCCEEDED:
        return None
    return classify_failure(
        bundle.result.state.value,
        bundle.result.failure_detail,
        policy.transient_markers,
        policy.semantic_markers,
        policy.code_markers,
        policy.ambiguous_markers,
    )


def repair_basis_text(
    failure_class: ExecutionFailureClass, attempts: tuple[RecoveryAttempt, ...]
) -> str:
    repair_basis = (
        f"create a new candidate after {failure_class.value}: "
        f"{attempts[-1].failure_detail or 'unspecified failure'}"
    )
    return repair_basis


def recovery_candidate(
    ids: IdGeneratorPort,
    action_plan: ActionPlan,
    action_id: str,
    baseline_digest: str,
    failure_class: ExecutionFailureClass,
    repair_basis: str,
    attempts: tuple[RecoveryAttempt, ...],
) -> RecoveryCandidateRevision:
    original_specification = next(
        item.specification for item in action_plan.alternatives if item.action_id == action_id
    )
    candidate_draft: dict[str, object] = {
        "candidate_id": ids.new("recovery-candidate"),
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
    return candidate


def reconciliation_record(
    ids: IdGeneratorPort, action_id: str, attempts: tuple[RecoveryAttempt, ...]
) -> ReconciliationRecord:
    reconciliation_draft: dict[str, object] = {
        "reconciliation_id": ids.new("reconciliation"),
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
    return reconciliation


def terminal_state(failure_class: ExecutionFailureClass, retry_budget_exhausted: bool) -> str:
    terminal = (
        "RECONCILIATION_REQUIRED"
        if failure_class == ExecutionFailureClass.AMBIGUOUS_EXTERNAL
        else "RETRY_BUDGET_EXHAUSTED"
        if retry_budget_exhausted
        else "CANDIDATE_REVISION_CREATED"
        if failure_class in {ExecutionFailureClass.SEMANTIC, ExecutionFailureClass.CODE}
        else "FAILED"
    )
    return terminal


def stage_revision(
    ids: IdGeneratorPort,
    clock: ClockPort,
    project_id: str,
    entity_type: EntityType,
    entity_id: str,
    content: dict[str, object],
    heads: dict[str, str],
) -> StagedRevision:
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    parent = heads.get(f"{entity_type.value}:{entity_id}")
    revision_draft: dict[str, object] = {
        "revision_id": ids.new("revision"),
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
        "created_at": clock.now(),
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
