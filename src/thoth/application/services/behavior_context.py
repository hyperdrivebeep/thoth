"""Request-local immutable policy selection and measured use collection."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter_ns

from thoth.application.services.evidence_context import (
    EvidenceContextSelection,
    select_evidence_context,
)
from thoth.domain.action import ActionPlan
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorUse
from thoth.domain.behavior_policy import (
    BehaviorPolicyError,
    RetrievalBehaviorPolicy,
    WorkflowBehaviorPolicy,
    workflow_repair_decision,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.ports.behavior_execution import BehaviorWorkControlPort


@dataclass
class BehaviorWorkContext:
    project_id: str
    snapshots: tuple[ActiveBehaviorSnapshot, ...]
    uses: list[BehaviorUse] = field(default_factory=list)
    shadows: tuple[ActiveBehaviorSnapshot, ...] = ()
    control: BehaviorWorkControlPort | None = None

    def snapshot(self, component: BehaviorArtifactKind) -> ActiveBehaviorSnapshot:
        matches = [item for item in self.snapshots if item.component == component]
        if len(matches) != 1:
            raise BehaviorPolicyError("BEHAVIOR_SNAPSHOT_UNAVAILABLE")
        return matches[0]


_CURRENT: ContextVar[BehaviorWorkContext | None] = ContextVar("behavior_work", default=None)


def current_behavior_work() -> BehaviorWorkContext | None:
    return _CURRENT.get()


@contextmanager
def behavior_work_scope(context: BehaviorWorkContext) -> Generator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def select_evidence_for_behavior(
    *,
    problem: str,
    evidence: tuple[EvidenceSpan, ...],
    forced_refs: tuple[str, ...] = (),
) -> EvidenceContextSelection:
    context = _CURRENT.get()
    if context is None:
        return select_evidence_context(problem=problem, evidence=evidence, forced_refs=forced_refs)
    snapshot = context.snapshot(BehaviorArtifactKind.RETRIEVAL_POLICY)
    policy = snapshot.policy
    if not isinstance(policy, RetrievalBehaviorPolicy):
        raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
    started = perf_counter_ns()
    result = select_evidence_context(
        problem=problem,
        evidence=evidence,
        forced_refs=forced_refs,
        max_spans=policy.max_spans,
        character_budget=policy.character_budget,
    )
    context.uses.append(
        BehaviorUse(
            component=snapshot.component,
            snapshot_digest=snapshot.snapshot_digest,
            operation="EVIDENCE_SELECTION",
            input_digest=domain_digest(
                "BEHAVIOR_SELECTION_INPUT",
                "1.0.0",
                canonical_payload(
                    {
                        "problem": problem,
                        "evidence": evidence,
                        "forced_refs": forced_refs,
                    }
                ),
            ),
            output_digest=domain_digest(
                "BEHAVIOR_SELECTION_OUTPUT", "1.0.0", canonical_payload(result)
            ),
            elapsed_ns=perf_counter_ns() - started,
            billed_cost_microunits=0,
            cost_basis="NOT_BILLABLE",
        )
    )
    for shadow in context.shadows:
        if shadow.component != BehaviorArtifactKind.RETRIEVAL_POLICY:
            continue
        shadow_policy = shadow.policy
        if not isinstance(shadow_policy, RetrievalBehaviorPolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        started = perf_counter_ns()
        operation = "EVIDENCE_SELECTION"
        try:
            shadow_result = select_evidence_context(
                problem=problem,
                evidence=evidence,
                forced_refs=forced_refs,
                max_spans=shadow_policy.max_spans,
                character_budget=shadow_policy.character_budget,
            )
            output = domain_digest(
                "BEHAVIOR_SELECTION_OUTPUT", "1.0.0", canonical_payload(shadow_result)
            )
        except ValueError:
            operation = "POLICY_HELD"
            output = domain_digest(
                "BEHAVIOR_POLICY_HELD",
                "1.0.0",
                canonical_payload({"reason_code": "SHADOW_SELECTION_HELD"}),
            )
        context.uses.append(
            BehaviorUse(
                component=shadow.component,
                snapshot_digest=shadow.snapshot_digest,
                operation=operation,
                input_digest=domain_digest(
                    "BEHAVIOR_SELECTION_INPUT",
                    "1.0.0",
                    canonical_payload(
                        {"problem": problem, "evidence": evidence, "forced_refs": forced_refs}
                    ),
                ),
                output_digest=output,
                elapsed_ns=perf_counter_ns() - started,
                billed_cost_microunits=0,
                cost_basis="NOT_BILLABLE",
            )
        )
    return result


def should_semantic_repair(needs_repair: bool) -> bool:
    context = _CURRENT.get()
    if context is None:
        return needs_repair
    snapshot = context.snapshot(BehaviorArtifactKind.WORKFLOW_DEFINITION)
    policy = snapshot.policy
    if not isinstance(policy, WorkflowBehaviorPolicy):
        raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
    started = perf_counter_ns()
    allowed = workflow_repair_decision(policy, needs_repair) != "HELD"
    context.uses.append(
        BehaviorUse(
            component=snapshot.component,
            snapshot_digest=snapshot.snapshot_digest,
            operation="WORKFLOW_POLICY",
            input_digest=domain_digest(
                "BEHAVIOR_WORKFLOW_INPUT",
                "1.0.0",
                canonical_payload({"needs_repair": needs_repair}),
            ),
            output_digest=domain_digest(
                "BEHAVIOR_WORKFLOW_OUTPUT", "1.0.0", canonical_payload({"allowed": allowed})
            ),
            elapsed_ns=perf_counter_ns() - started,
            billed_cost_microunits=0,
            cost_basis="NOT_BILLABLE",
        )
    )
    for shadow in context.shadows:
        if shadow.component != BehaviorArtifactKind.WORKFLOW_DEFINITION:
            continue
        shadow_policy = shadow.policy
        if not isinstance(shadow_policy, WorkflowBehaviorPolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        started = perf_counter_ns()
        decision = workflow_repair_decision(shadow_policy, needs_repair)
        context.uses.append(
            BehaviorUse(
                component=shadow.component,
                snapshot_digest=shadow.snapshot_digest,
                operation="WORKFLOW_POLICY",
                input_digest=domain_digest(
                    "BEHAVIOR_WORKFLOW_INPUT",
                    "1.0.0",
                    canonical_payload({"needs_repair": needs_repair}),
                ),
                output_digest=domain_digest(
                    "BEHAVIOR_WORKFLOW_DECISION", "1.0.0", canonical_payload({"decision": decision})
                ),
                elapsed_ns=perf_counter_ns() - started,
                billed_cost_microunits=0,
                cost_basis="NOT_BILLABLE",
            )
        )
    if not allowed:
        raise BehaviorPolicyError("BEHAVIOR_SEMANTIC_REPAIR_DISABLED")
    return needs_repair


def require_compiled_behavior_plan(plan: ActionPlan | None) -> ActionPlan:
    if plan is None:
        raise BehaviorPolicyError("BEHAVIOR_ACTION_PLAN_HELD")
    return plan
