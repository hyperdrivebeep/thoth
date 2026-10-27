"""Use the active N06 retrieval policy and its measured-use journal for every stage."""

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter_ns

from thoth.application.services.behavior_context import behavior_work_scope, current_behavior_work
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorUse
from thoth.domain.behavior_policy import (
    BehaviorPolicyError,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
    RetrievalPolicy,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan


def replay_retrieval_shadows(selection: Callable[[], object]) -> None:
    """Run a stage again under each retrieval shadow, for stages with no shared selection function.

    The candidate stage does not use this: it hands the same request and the shadow's own policy to
    the selection function itself (see lexical_selection).
    """
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


def retrieval_policy() -> RetrievalPolicy:
    work = current_behavior_work()
    if work is None:
        return RetrievalBehaviorPolicy()
    policy = work.snapshot(BehaviorArtifactKind.RETRIEVAL_POLICY).policy
    if not isinstance(policy, RetrievalBehaviorPolicy | RetrievalLanePolicy):
        raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
    return policy


def retrieval_shadows() -> tuple[tuple[ActiveBehaviorSnapshot, RetrievalPolicy], ...]:
    """Each retrieval shadow of this request with its policy, in the order they were armed."""
    work = current_behavior_work()
    if work is None:
        return ()
    shadows: list[tuple[ActiveBehaviorSnapshot, RetrievalPolicy]] = []
    for shadow in work.shadows:
        if shadow.component != BehaviorArtifactKind.RETRIEVAL_POLICY:
            continue
        if not isinstance(shadow.policy, RetrievalBehaviorPolicy | RetrievalLanePolicy):
            raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
        shadows.append((shadow, shadow.policy))
    return tuple(shadows)


def record_selection(
    stage: str,
    inputs: object,
    result: tuple[EvidenceSpan, ...],
    started: int,
    snapshot: ActiveBehaviorSnapshot | None = None,
) -> None:
    """Journal one selection against the active policy, or against the shadow snapshot given."""
    work = current_behavior_work()
    if work is None:
        return
    if snapshot is None:
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
