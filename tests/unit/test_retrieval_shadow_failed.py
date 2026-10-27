"""G3 1c: a shadow that breaks is held and its request goes on; the active path is never caught.

A shadow that is refused (a ValueError) keeps its reason code SHADOW_SELECTION_HELD and its digest.
A shadow that breaks for another reason (any Exception except BaseException such as cancellation)
is journaled as POLICY_HELD under SHADOW_SELECTION_FAILED. A defect in the active selection, or in
a lane run on its own, still surfaces.
"""

import asyncio
from typing import Any

import pytest
from tests.unit.test_retrieval_policy_v3_research_path import snapshot
from tests.unit.test_retrieval_shadow_held import ACTIVE, EXACT, HELD_OUTPUT, SOURCE

from thoth.application.services import behavior_context, research_retrieval
from thoth.application.services.behavior_context import (
    BehaviorWorkContext,
    behavior_work_scope,
    current_behavior_work,
    select_evidence_for_behavior,
)
from thoth.application.services.evidence_context import EvidenceContextSelection
from thoth.application.services.research_retrieval import adjacent_packet, lexical_candidates
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorUse
from thoth.domain.behavior_policy import RetrievalFusionPolicy, RetrievalLanePolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_lane import (
    GROUPED_BM25,
    LANES,
    PROTECTED_EXACT,
    Lane,
    RetrievalRequest,
    select_retrieval_context,
)

FAILED_OUTPUT = domain_digest(
    "BEHAVIOR_POLICY_HELD", "1.0.0", canonical_payload({"reason_code": "SHADOW_SELECTION_FAILED"})
)
FUSED = RetrievalFusionPolicy.model_validate(
    {"lanes": [{"lane_id": PROTECTED_EXACT, "depth": 3}, {"lane_id": GROUPED_BM25, "depth": 3}]}
)


def breaks(error: BaseException) -> Lane:
    def lane(
        request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
    ) -> tuple[EvidenceSpan, ...]:
        raise error

    return lane


def run(
    *shadows: ActiveBehaviorSnapshot, active: ActiveBehaviorSnapshot | None = None
) -> tuple[list[str], list[BehaviorUse]]:
    context = BehaviorWorkContext("p", (active or snapshot(ACTIVE),), shadows=shadows)
    with behavior_work_scope(context):
        chosen = lexical_candidates("alpha", (), SOURCE)
    return [s.span_id for s in chosen], context.uses


@pytest.mark.parametrize("error", [RuntimeError("boom"), KeyError("region"), IndexError("i")])
@pytest.mark.parametrize("policy", [EXACT, FUSED], ids=["3.0.0", "4.0.0"])
def test_a_shadow_that_breaks_is_held_as_failed_and_the_request_goes_on(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    policy: RetrievalLanePolicy | RetrievalFusionPolicy,
) -> None:
    plain, _ = run()
    shadow = snapshot(policy, "SHADOW")
    monkeypatch.setitem(LANES, PROTECTED_EXACT, breaks(error))
    chosen, uses = run(shadow)
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
    failed = uses[1]
    assert failed.snapshot_digest == shadow.snapshot_digest
    assert failed.output_digest == FAILED_OUTPUT != HELD_OUTPUT
    assert failed.input_digest == uses[0].input_digest


def test_a_refused_shadow_keeps_its_reason_and_its_digest() -> None:
    assert (
        domain_digest(
            "BEHAVIOR_POLICY_HELD",
            "1.0.0",
            canonical_payload({"reason_code": "SHADOW_SELECTION_HELD"}),
        )
        == HELD_OUTPUT
    )


def test_the_active_selection_is_not_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(LANES, PROTECTED_EXACT, breaks(RuntimeError("defect")))
    with pytest.raises(RuntimeError, match="defect"):
        run(active=snapshot(EXACT))
    request = RetrievalRequest("alpha", (), SOURCE)
    with pytest.raises(RuntimeError, match="defect"):
        select_retrieval_context(request, EXACT)
    with pytest.raises(RuntimeError, match="defect"):
        select_retrieval_context(request, FUSED)


def test_a_cancellation_is_not_a_held_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(LANES, PROTECTED_EXACT, breaks(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        run(snapshot(EXACT, "SHADOW"))


def test_the_adjacent_packet_holds_a_shadow_that_breaks_and_keeps_its_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranking = tuple(s.span_id for s in SOURCE[:2])
    original = research_retrieval.text_neighbors

    def neighbors(span: EvidenceSpan, source: tuple[EvidenceSpan, ...]) -> tuple[EvidenceSpan, ...]:
        work = current_behavior_work()
        assert work is not None
        if work.snapshots[0].origin == "SHADOW":
            raise RuntimeError("shadow packet")
        return original(span, source)

    def packet(*shadows: ActiveBehaviorSnapshot) -> tuple[list[str], list[BehaviorUse]]:
        context = BehaviorWorkContext("p", (snapshot(ACTIVE),), shadows=shadows)
        with behavior_work_scope(context):
            chosen = adjacent_packet(ranking, SOURCE[:2], SOURCE, "alpha")
        return [s.span_id for s in chosen], context.uses

    plain, _ = packet()
    monkeypatch.setattr(research_retrieval, "text_neighbors", neighbors)
    chosen, uses = packet(snapshot(RetrievalLanePolicy(max_spans=1), "SHADOW"))
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
    assert uses[1].output_digest == FAILED_OUTPUT


def test_the_older_analysis_path_holds_a_shadow_that_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = behavior_context.select_evidence_context

    def selection(**values: Any) -> EvidenceContextSelection:
        if values.get("max_spans") == 5:
            raise RuntimeError("shadow analysis")
        return original(**values)

    def analysis(*shadows: ActiveBehaviorSnapshot) -> tuple[list[str], list[BehaviorUse]]:
        context = BehaviorWorkContext("p", (snapshot(ACTIVE),), shadows=shadows)
        with behavior_work_scope(context):
            chosen = select_evidence_for_behavior(problem="alpha", evidence=SOURCE)
        return [s.span_id for s in chosen.selected], context.uses

    plain, _ = analysis()
    monkeypatch.setattr(behavior_context, "select_evidence_context", selection)
    chosen, uses = analysis(snapshot(RetrievalLanePolicy(max_spans=5), "SHADOW"))
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
    assert uses[1].output_digest == FAILED_OUTPUT


def test_a_fused_shadow_is_replayed_like_any_other_policy() -> None:
    plain, _ = run()
    shadow = snapshot(FUSED, "SHADOW")
    chosen, uses = run(shadow)
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "EVIDENCE_SELECTION"]
    assert uses[1].snapshot_digest == shadow.snapshot_digest
    assert uses[1].input_digest != uses[0].input_digest  # the journal names the lanes it ran
