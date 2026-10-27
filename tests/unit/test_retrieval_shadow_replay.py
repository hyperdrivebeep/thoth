"""S4: a retrieval shadow is replayed by the one selection function and never steers the run."""

import pytest
from tests.unit.test_retrieval_policy_v3_research_path import snapshot
from tests.unit.test_retrieval_selection_oracle import Scenario, build_scenarios, span

from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.research_retrieval import adjacent_packet, lexical_candidates
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorUse
from thoth.domain.behavior_policy import (
    PromptBehaviorPolicy,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
)
from thoth.domain.evaluation_run import sealed_payload
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_lane import LANES, LEXICAL_SUBSTRING_V1, RetrievalRequest

type Policy = RetrievalBehaviorPolicy | RetrievalLanePolicy

# Shadows that choose differently from the scenarios' default budgets.
SHADOWS: tuple[Policy, ...] = (
    RetrievalBehaviorPolicy(max_spans=1),
    RetrievalLanePolicy(max_spans=2, character_budget=120),
    RetrievalLanePolicy(),
)
SCENARIOS = build_scenarios()


def run(
    scenario: Scenario, active: Policy, shadows: tuple[Policy, ...] = ()
) -> tuple[list[str], list[BehaviorUse]]:
    context = BehaviorWorkContext(
        "p", (snapshot(active),), shadows=tuple(snapshot(p, "SHADOW") for p in shadows)
    )
    with behavior_work_scope(context):
        selected = lexical_candidates(
            scenario.question,
            scenario.queries,
            scenario.evidence,
            scenario.pinned,
            context=scenario.context,
        )
    return [s.span_id for s in selected], context.uses


def record(use: BehaviorUse) -> tuple[str, str, str, str]:
    return use.component, use.operation, use.input_digest, use.output_digest


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_shadow_never_changes_the_selection_order(name: str) -> None:
    scenario = SCENARIOS[name]
    plain, _ = run(scenario, scenario.policy)
    shadowed, uses = run(scenario, scenario.policy, SHADOWS)
    assert shadowed == plain
    assert len(uses) == 1 + len(SHADOWS)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_shadow_use_equals_the_use_the_same_policy_records_when_active(name: str) -> None:
    scenario = SCENARIOS[name]
    _, shadowed = run(scenario, scenario.policy, SHADOWS)
    for position, policy in enumerate(SHADOWS, start=1):
        _, alone = run(scenario, policy)
        assert record(shadowed[position]) == record(alone[0])
        assert shadowed[position].snapshot_digest == snapshot(policy, "SHADOW").snapshot_digest


def test_the_shadows_choose_differently_so_the_equality_above_is_not_vacuous() -> None:
    scenario = SCENARIOS["no_context_uses_the_ranked_path"]
    _, uses = run(scenario, scenario.policy, SHADOWS)
    assert len({use.output_digest for use in uses}) > 1


def test_the_active_and_the_shadow_policy_rank_through_the_same_registered_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[RetrievalRequest] = []
    original = LANES[LEXICAL_SUBSTRING_V1]

    def spy(
        request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
    ) -> tuple[EvidenceSpan, ...]:
        requests.append(request)
        return original(request, eligible)

    monkeypatch.setitem(LANES, LEXICAL_SUBSTRING_V1, spy)
    scenario = SCENARIOS["no_context_uses_the_ranked_path"]
    run(scenario, scenario.policy, (RetrievalLanePolicy(max_spans=2),))
    assert len(requests) == 2 and requests[0] == requests[1]


def test_a_shadow_of_another_component_and_a_run_without_scope_record_no_selection() -> None:
    prompt = ActiveBehaviorSnapshot.model_validate(
        sealed_payload(
            "ACTIVE_BEHAVIOR_SNAPSHOT",
            "snapshot_digest",
            {
                "project_id": "p",
                "component": "PROMPT_BUNDLE",
                "environment": "LOCAL",
                "artifact_ref": None,
                "content_digest": "0" * 64,
                "policy": PromptBehaviorPolicy(task_guidance="shadow guidance"),
                "origin": "SHADOW",
                "registry_revision": 0,
                "exposure_ref": None,
                "ignored_registry_digest": None,
                "reason_code": None,
            },
        )
    )
    scenario = SCENARIOS["no_context_uses_the_ranked_path"]
    context = BehaviorWorkContext("p", (snapshot(scenario.policy),), shadows=(prompt,))
    with behavior_work_scope(context):
        selected = lexical_candidates(scenario.question, (), scenario.evidence)
    assert len(context.uses) == 1
    assert [s.span_id for s in selected] == run(scenario, scenario.policy)[0]
    bare = lexical_candidates(scenario.question, (), scenario.evidence)
    assert [s.span_id for s in bare] == [s.span_id for s in selected]


def test_the_adjacent_packet_replays_each_shadow_without_changing_the_packet() -> None:
    spans = (
        span("n1", "alpha reading one in the first block of the source text", line=1),
        span("n2", "alpha reading two in the first block of the source text", line=2),
        span("n3", "alpha reading three in the first block of the source text", line=3),
    )
    ranking = tuple(s.span_id for s in spans)

    def packet(shadows: tuple[Policy, ...]) -> tuple[list[str], list[BehaviorUse]]:
        context = BehaviorWorkContext(
            "p",
            (snapshot(RetrievalBehaviorPolicy()),),
            shadows=tuple(snapshot(p, "SHADOW") for p in shadows),
        )
        with behavior_work_scope(context):
            chosen = adjacent_packet(ranking, spans, spans, "alpha")
        return [s.span_id for s in chosen], context.uses

    plain, _ = packet(())
    shadowed, uses = packet((RetrievalBehaviorPolicy(max_spans=1),))
    assert shadowed == plain and len(plain) == 3
    assert len(uses) == 2 and uses[0].output_digest != uses[1].output_digest
