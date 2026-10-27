"""G3 1a: a retrieval shadow that cannot select is held; the request goes on."""

import pytest
from tests.unit.test_retrieval_policy_v3_research_path import snapshot
from tests.unit.test_retrieval_selection_oracle import span

from thoth.application.services.behavior_context import (
    BehaviorWorkContext,
    behavior_work_scope,
    select_evidence_for_behavior,
)
from thoth.application.services.research_retrieval import adjacent_packet, lexical_candidates
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorUse
from thoth.domain.behavior_policy import (
    PromptBehaviorPolicy,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.retrieval_lane import LANE_VERSIONS, LANES, PROTECTED_EXACT

SOURCE = (
    span("a", "alpha reading one in the first block of the source text", line=1),
    span("b", "alpha reading two in the first block of the source text", line=2),
    span("c", "a closing remark that does not match the question at all", line=3),
)
ACTIVE = RetrievalBehaviorPolicy()
EXACT = RetrievalLanePolicy.model_validate({"lanes": [{"lane_id": PROTECTED_EXACT}]})
HELD_OUTPUT = domain_digest(
    "BEHAVIOR_POLICY_HELD", "1.0.0", canonical_payload({"reason_code": "SHADOW_SELECTION_HELD"})
)


def mismatched() -> ActiveBehaviorSnapshot:
    """A retrieval shadow whose policy is not a retrieval policy (validation would refuse it)."""
    return snapshot(RetrievalBehaviorPolicy(), "SHADOW").model_copy(
        update={"policy": PromptBehaviorPolicy()}
    )


def run(*shadows: ActiveBehaviorSnapshot) -> tuple[list[str], list[BehaviorUse]]:
    context = BehaviorWorkContext("p", (snapshot(ACTIVE),), shadows=shadows)
    with behavior_work_scope(context):
        chosen = lexical_candidates("alpha", (), SOURCE)
    return [s.span_id for s in chosen], context.uses


def test_a_shadow_naming_an_unregistered_lane_version_is_held_and_the_request_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain, _ = run()
    shadow = snapshot(EXACT, "SHADOW")
    monkeypatch.setitem(LANE_VERSIONS, PROTECTED_EXACT, "1.1.0")
    chosen, uses = run(shadow)
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
    held = uses[1]
    assert held.snapshot_digest == shadow.snapshot_digest and held.output_digest == HELD_OUTPUT
    assert held.input_digest == uses[0].input_digest


def test_a_shadow_naming_an_unregistered_lane_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    plain, _ = run()
    monkeypatch.delitem(LANES, PROTECTED_EXACT)
    chosen, uses = run(snapshot(EXACT, "SHADOW"))
    assert chosen == plain and [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]


def test_each_shadow_is_judged_alone_and_the_order_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = snapshot(RetrievalLanePolicy(max_spans=1), "SHADOW")
    bad = snapshot(EXACT, "SHADOW")
    monkeypatch.setitem(LANE_VERSIONS, PROTECTED_EXACT, "1.1.0")
    plain, _ = run()
    chosen, uses = run(bad, good, mismatched())
    assert chosen == plain
    assert [u.operation for u in uses] == [
        "EVIDENCE_SELECTION",
        "POLICY_HELD",
        "EVIDENCE_SELECTION",
        "POLICY_HELD",
    ]
    assert uses[2].snapshot_digest == good.snapshot_digest


def test_a_shadow_of_the_wrong_kind_is_held_instead_of_failing_the_request() -> None:
    plain, _ = run()
    chosen, uses = run(mismatched())
    assert chosen == plain and [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]


def test_the_adjacent_packet_holds_a_shadow_that_fails_and_keeps_its_packet() -> None:
    ranking = tuple(s.span_id for s in SOURCE[:2])

    def packet(*shadows: ActiveBehaviorSnapshot) -> tuple[list[str], list[BehaviorUse]]:
        context = BehaviorWorkContext("p", (snapshot(ACTIVE),), shadows=shadows)
        with behavior_work_scope(context):
            chosen = adjacent_packet(ranking, SOURCE[:2], SOURCE, "alpha")
        return [s.span_id for s in chosen], context.uses

    plain, _ = packet()
    chosen, uses = packet(mismatched())
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
    assert uses[1].output_digest == HELD_OUTPUT


def test_the_older_analysis_path_holds_a_shadow_of_the_wrong_kind_in_the_same_way() -> None:
    def analysis(*shadows: ActiveBehaviorSnapshot) -> tuple[list[str], list[BehaviorUse]]:
        context = BehaviorWorkContext("p", (snapshot(ACTIVE),), shadows=shadows)
        with behavior_work_scope(context):
            chosen = select_evidence_for_behavior(problem="alpha", evidence=SOURCE)
        return [s.span_id for s in chosen.selected], context.uses

    plain, _ = analysis()
    chosen, uses = analysis(mismatched())
    assert chosen == plain
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
    assert uses[1].output_digest == HELD_OUTPUT
