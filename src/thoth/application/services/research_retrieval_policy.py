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


def replay_retrieval_shadows(selection: Callable[[], object], inputs: object = None) -> None:
    """Run a stage again under each retrieval shadow, for stages with no shared selection function.

    The candidate stage does not use this: it hands the same request and the shadow's own policy to
    the selection function itself (see lexical_selection). A shadow that fails is journaled as
    held and the request goes on with the active result: a shadow never fails its request.
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
        started = perf_counter_ns()
        try:
            with behavior_work_scope(replace(work, snapshots=snapshots, shadows=())):
                selection()
        except ValueError:
            record_held_selection("CONTEXT_PACKET", inputs, started, shadow)


def retrieval_policy() -> RetrievalPolicy:
    work = current_behavior_work()
    if work is None:
        return RetrievalBehaviorPolicy()
    policy = work.snapshot(BehaviorArtifactKind.RETRIEVAL_POLICY).policy
    if not isinstance(policy, RetrievalBehaviorPolicy | RetrievalLanePolicy):
        raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
    return policy


def retrieval_shadows() -> tuple[tuple[ActiveBehaviorSnapshot, RetrievalPolicy | None], ...]:
    """Each retrieval shadow of this request with its policy, in the order they were armed.

    The policy is None for a shadow whose policy is not a retrieval policy; that shadow is held.
    """
    work = current_behavior_work()
    if work is None:
        return ()
    shadows: list[tuple[ActiveBehaviorSnapshot, RetrievalPolicy | None]] = []
    for shadow in work.shadows:
        if shadow.component != BehaviorArtifactKind.RETRIEVAL_POLICY:
            continue
        policy = shadow.policy
        shadows.append(
            (
                shadow,
                policy
                if isinstance(policy, RetrievalBehaviorPolicy | RetrievalLanePolicy)
                else None,
            )
        )
    return tuple(shadows)


def record_held_selection(
    stage: str, inputs: object, started: int, snapshot: ActiveBehaviorSnapshot
) -> None:
    """Journal a shadow selection that could not be made, the way the analysis path does."""
    work = current_behavior_work()
    if work is None:
        return
    work.uses.append(
        BehaviorUse(
            component=snapshot.component,
            snapshot_digest=snapshot.snapshot_digest,
            operation="POLICY_HELD",
            input_digest=domain_digest(
                "RETRIEVAL_INPUT", "1.0.0", canonical_payload({"stage": stage, "input": inputs})
            ),
            output_digest=domain_digest(
                "BEHAVIOR_POLICY_HELD",
                "1.0.0",
                canonical_payload({"reason_code": "SHADOW_SELECTION_HELD"}),
            ),
            elapsed_ns=perf_counter_ns() - started,
            billed_cost_microunits=0,
            cost_basis="NOT_BILLABLE",
        )
    )


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
