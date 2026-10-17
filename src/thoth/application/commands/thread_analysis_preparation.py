"""Prepare a cycle command without changing actor lookup or execution boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thoth.application.reducers import SufficiencySignals
from thoth.application.workflows import ThreadCycleCommand
from thoth.domain.action import ActionCompilationPolicy
from thoth.domain.actor import ActorRef
from thoth.domain.enums import ActorKind, RiskTier

if TYPE_CHECKING:
    from thoth.application.commands.thread_analysis import ThreadInputRequest
    from thoth.domain.auth import AuthenticatedActorContext
    from thoth.domain.criterion import CriterionCandidate
    from thoth.domain.evidence import EvidenceSpan
    from thoth.domain.project import Project, WorkThread


def build_cycle_command(
    request: ThreadInputRequest,
    project: Project,
    thread: WorkThread,
    analysis_problem: str,
    criteria: tuple[CriterionCandidate, ...],
    selected_evidence: tuple[EvidenceSpan, ...],
    initiator: AuthenticatedActorContext | None,
) -> ThreadCycleCommand:
    return ThreadCycleCommand(
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
            role_assignment_ref=(None if initiator is None else initiator.role_assignment_id),
        ),
        policy_version=project.policy_binding_ref,
        model_policy_ref=f"model-policy:{request.provider}-v1",
        expected_head_overrides=request.expected_head_overrides,
        memory_scope=thread.scope,
    )


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
