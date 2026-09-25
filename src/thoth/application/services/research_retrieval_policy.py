"""Use the active N06 retrieval policy and its measured-use journal for every stage."""

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter_ns

from thoth.application.services.behavior_context import behavior_work_scope, current_behavior_work
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import BehaviorUse
from thoth.domain.behavior_policy import BehaviorPolicyError, RetrievalBehaviorPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan


def replay_retrieval_shadows(selection: Callable[[], object]) -> None:
    work = current_behavior_work()
    if work is None:
        return
    for shadow in work.shadows:
        if shadow.component != BehaviorArtifactKind.RETRIEVAL_POLICY:
            continue
        snapshots = tuple(
            shadow if item.component == shadow.component else item for item in work.snapshots
        )
        with behavior_work_scope(replace(work, snapshots=snapshots, shadows=())):
            selection()


def retrieval_policy() -> RetrievalBehaviorPolicy:
    work = current_behavior_work()
    if work is None:
        return RetrievalBehaviorPolicy()
    policy = work.snapshot(BehaviorArtifactKind.RETRIEVAL_POLICY).policy
    if not isinstance(policy, RetrievalBehaviorPolicy):
        raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
    return policy


def record_selection(
    stage: str, inputs: object, result: tuple[EvidenceSpan, ...], started: int
) -> None:
    work = current_behavior_work()
    if work is None:
        return
    snapshot = work.snapshot(BehaviorArtifactKind.RETRIEVAL_POLICY)
    work.uses.append(
        BehaviorUse(
            component=snapshot.component,
            snapshot_digest=snapshot.snapshot_digest,
            operation="EVIDENCE_SELECTION",
            input_digest=domain_digest(
                "RETRIEVAL_INPUT", "1.0.0", canonical_payload({"stage": stage, "input": inputs})
            ),
            output_digest=domain_digest(
                "RETRIEVAL_OUTPUT", "1.0.0", canonical_payload({"evidence": result})
            ),
            elapsed_ns=perf_counter_ns() - started,
            billed_cost_microunits=0,
            cost_basis="NOT_BILLABLE",
        )
    )
