"""S3: the evaluation of a retrieval policy runs the research path's own selection function."""

from typing import cast

import pytest
from pydantic import JsonValue
from tests.unit.test_retrieval_selection_oracle import (
    EXPECTED,
    Scenario,
    build_scenarios,
    evaluation_payload,
    oracle_select,
    random_scenarios,
)

from thoth.adapters.behavior.registry import (
    RetrievalBehaviorHandler,
    RetrievalLaneHandler,
    default_behavior_components,
)
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    BehaviorPolicyError,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
)
from thoth.domain.retrieval_lane import RETRIEVAL_EVALUATOR_ID, evaluation_algorithm


def evaluate(
    handler: RetrievalBehaviorHandler | RetrievalLaneHandler,
    policy: RetrievalBehaviorPolicy | RetrievalLanePolicy,
    payload: dict[str, object],
) -> dict[str, JsonValue]:
    return handler.evaluate(policy, cast(dict[str, JsonValue], payload))


def both_versions(sc: Scenario) -> list[tuple[object, object]]:
    return [
        (RetrievalBehaviorHandler(), sc.policy),
        (
            RetrievalLaneHandler(),
            RetrievalLanePolicy(
                max_spans=sc.policy.max_spans, character_budget=sc.policy.character_budget
            ),
        ),
    ]


def selected(handler: object, policy: object, sc: Scenario) -> list[str]:
    result = evaluate(
        cast(RetrievalBehaviorHandler, handler),
        cast(RetrievalBehaviorPolicy, policy),
        evaluation_payload(sc),
    )
    return cast(list[str], result["selected_refs"])


@pytest.mark.parametrize("name", list(build_scenarios()))
def test_both_policy_versions_evaluate_to_the_research_selection(name: str) -> None:
    sc = build_scenarios()[name]
    for handler, policy in both_versions(sc):
        assert selected(handler, policy, sc) == EXPECTED[name]


def test_random_corpora_evaluate_to_the_research_selection_in_both_versions() -> None:
    for sc in random_scenarios():
        wanted = oracle_select(
            sc.question, sc.queries, sc.evidence, sc.pinned, sc.context, sc.policy
        )
        for handler, policy in both_versions(sc):
            assert selected(handler, policy, sc) == wanted


def test_the_output_records_the_evaluation_algorithm_and_keeps_the_old_keys() -> None:
    sc = build_scenarios()["artifacts_are_interleaved"]
    for handler, policy in both_versions(sc):
        out = evaluate(
            cast(RetrievalBehaviorHandler, handler),
            cast(RetrievalBehaviorPolicy, policy),
            evaluation_payload(sc),
        )
        assert set(out) == {
            "selected_refs",
            "excluded_count",
            "selected_characters",
            "mode",
            "evaluator",
        }
        assert out["evaluator"] == RETRIEVAL_EVALUATOR_ID == "retrieval-lane-dispatch:1.0.0"
        assert out["mode"] == "LEGACY"
        chosen = {s.span_id: s for s in sc.evidence}
        assert out["excluded_count"] == len(sc.evidence) - 5
        assert out["selected_characters"] == sum(
            len(chosen[ref].exact_text) for ref in cast(list[str], out["selected_refs"])
        )


def test_only_a_retrieval_comparison_has_an_evaluation_algorithm() -> None:
    assert evaluation_algorithm(BehaviorArtifactKind.RETRIEVAL_POLICY) == RETRIEVAL_EVALUATOR_ID
    for kind in BehaviorArtifactKind:
        if kind != BehaviorArtifactKind.RETRIEVAL_POLICY:
            assert evaluation_algorithm(kind) is None


def test_a_case_written_for_the_old_evaluation_still_validates_and_runs() -> None:
    sc = build_scenarios()["pinned_first_and_denied_pins_are_ignored"]
    old_case: dict[str, object] = {
        "problem": sc.question,
        "evidence": [s.model_dump(mode="json") for s in sc.evidence],
        "forced_refs": ["span:low"],
    }
    out = evaluate(RetrievalBehaviorHandler(), sc.policy, old_case)
    assert cast(list[str], out["selected_refs"])[0] == "span:low"


def test_forced_refs_mean_pinned_refs_and_a_pin_outside_the_eligible_spans_is_ignored() -> None:
    sc = build_scenarios()["pinned_first_and_denied_pins_are_ignored"]
    base = evaluation_payload(sc)
    as_forced = {**base, "pinned_refs": [], "forced_refs": ["span:low", "span:denied"]}
    both = {**base, "forced_refs": ["span:low"], "pinned_refs": ["span:low", "span:denied"]}
    handler = RetrievalBehaviorHandler()
    expected = EXPECTED["pinned_first_and_denied_pins_are_ignored"]
    # An ineligible pin used to stop the old evaluation with an error; it is ignored now.
    assert evaluate(handler, sc.policy, as_forced)["selected_refs"] == expected
    assert evaluate(handler, sc.policy, both)["selected_refs"] == expected


def test_each_handler_refuses_the_other_versions_policy() -> None:
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_KIND_MISMATCH"):
        RetrievalBehaviorHandler().evaluate(RetrievalLanePolicy(), {"problem": "q", "evidence": []})
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_KIND_MISMATCH"):
        RetrievalLaneHandler().evaluate(RetrievalBehaviorPolicy(), {"problem": "q", "evidence": []})


def test_the_handlers_are_registered_and_the_baseline_version_is_still_2_0_0() -> None:
    registry = default_behavior_components()
    kind = BehaviorArtifactKind.RETRIEVAL_POLICY
    assert registry.resolve(kind, "2.0.0").default_policy() == RetrievalBehaviorPolicy()
    assert registry.resolve(kind, "3.0.0").default_policy() == RetrievalLanePolicy()
    assert registry.components() == tuple(
        sorted({BehaviorArtifactKind.PROMPT_BUNDLE, kind, BehaviorArtifactKind.WORKFLOW_DEFINITION})
    )
    # 4.0.0 is registered since G3 1c (it was the first version the registry did not know).
    assert registry.resolve(kind, "4.0.0").version == "4.0.0"
    assert registry.resolve(kind, "5.0.0").version == "5.0.0"
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        registry.resolve(kind, "5.0.0").default_policy()
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_COMPONENT_NOT_CONFIGURED"):
        registry.resolve(kind, "6.0.0")


def test_a_wider_budget_is_proposed_for_either_version_after_a_semantic_failure() -> None:
    for handler, policy in (
        (RetrievalBehaviorHandler(), RetrievalBehaviorPolicy(max_spans=10)),
        (RetrievalLaneHandler(), RetrievalLanePolicy(max_spans=10)),
    ):
        (proposed,) = handler.propose(policy, "SEMANTIC")
        assert proposed.version == policy.version
        assert cast(RetrievalBehaviorPolicy, proposed).max_spans == 20
        assert handler.propose(policy, "OTHER") == ()
