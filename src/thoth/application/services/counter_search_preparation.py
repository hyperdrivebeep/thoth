"""Pure specifications for bounded counter-search operations."""

from typing import TypedDict

from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.counterevidence import CounterSearchPlan
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.hypothesis import Hypothesis
from thoth.domain.policy import (
    AuthoritativeExecutionPolicy,
    CounterSearchLimits,
    CounterSearchRoute,
)
from thoth.domain.project import Project, WorkThread


class MultiWaveInvestigationSpec(TypedDict):
    thread: WorkThread
    trigger: str
    question: str
    target_object_id: str
    target_hypothesis_id: str
    mode: str
    scope: dict[str, str]
    required_evidence_groups: tuple[str, ...]
    query_families: tuple[str, ...]
    budget: int
    stop_conditions: tuple[str, ...]


def multi_wave_investigation_spec(
    project: Project,
    thread: WorkThread,
    hypothesis: Hypothesis,
    policy: AuthoritativeExecutionPolicy,
    ordered: tuple[CounterSearchRoute, ...],
    limits: CounterSearchLimits,
) -> MultiWaveInvestigationSpec:
    return {
        "thread": thread,
        "trigger": "HIGH_RISK_MULTI_WAVE_COUNTER_SEARCH",
        "question": f"Run bounded multi-wave challenge for {hypothesis.hypothesis_id}",
        "target_object_id": hypothesis.object_id,
        "target_hypothesis_id": hypothesis.hypothesis_id,
        "mode": "CRITICAL",
        "scope": {
            **thread.scope,
            "cutoff": project.cutoff_at.isoformat(),
            "access": "PROJECT_POLICY_BOUND",
            "egress": "PROJECT_POLICY_BOUND",
            "security": policy.max_source_security_class.value,
            "max_depth": str(limits.max_depth),
        },
        "required_evidence_groups": (f"counterevidence:{hypothesis.hypothesis_id}",),
        "query_families": tuple(
            dict.fromkeys(family for route in ordered for family in route.query_families)
        ),
        "budget": limits.max_waves,
        "stop_conditions": (
            "INDEPENDENT_CONFIRMATION_CONVERGED",
            "SEARCH_SATURATED",
            "BUDGET_EXHAUSTED",
            "POLICY_BLOCKED",
            "AUTHORITY_REQUIRED",
            "ABSTAINED",
        ),
    }


def multi_wave_connector_request(
    project: Project,
    plan: CounterSearchPlan,
    policy: AuthoritativeExecutionPolicy,
    remaining_bytes: int,
) -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:critical-counter-search-coordinator",
        project_id=project.project_id,
        connector_id=plan.connector_id,
        selector=plan.selector,
        authority=AuthorityState(plan.source_authority),
        cutoff_state=CutoffState(plan.temporal_state),
        security_class=SecurityClass.INTERNAL,
        cutoff_at=project.cutoff_at,
        max_bytes=remaining_bytes,
        policy_id=policy.policy_id,
        policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest,
    )
