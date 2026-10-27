"""G3 1c: retrieval policy 4.0.0 (fused lanes) and the registry paths that enumerate handlers."""

from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    RetrievalBehaviorPolicy,
    RetrievalFusionPolicy,
    RetrievalLanePolicy,
    is_retrieval_policy,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import BehaviorEvaluationProgram
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort

POLICY: TypeAdapter[Any] = TypeAdapter(BehaviorPolicy)
KIND = BehaviorArtifactKind.RETRIEVAL_POLICY

# Taken from the first serialization of 4.0.0 and pinned: a stored 4.0.0 policy keeps its bytes.
POLICY_DIGEST = "ff806dea77f1f4cdb7721908caeafd85579e82d57d2dccbe751b8034e238ab68"
CONTENT_DIGEST = "979ad01ff930d87cef0c92b2f7e2abe750df57ff2f7f81aa8058ac41ca7b44f5"


def lanes(*pairs: tuple[str, int]) -> list[dict[str, object]]:
    return [{"lane_id": lane, "depth": depth} for lane, depth in pairs]


def policy(*pairs: tuple[str, int]) -> RetrievalFusionPolicy:
    return RetrievalFusionPolicy.model_validate({"lanes": lanes(*pairs)})


BASE = policy(("GROUPED_BM25", 3), ("PROTECTED_EXACT", 12))


def test_the_serialized_form_and_digests_are_pinned() -> None:
    assert BASE.model_dump(mode="json") == {
        "kind": "RETRIEVAL_POLICY",
        "version": "4.0.0",
        "max_spans": 160,
        "character_budget": 50000,
        "lanes": [
            {"lane_id": "GROUPED_BM25", "lane_version": "1.0.0", "depth": 3},
            {"lane_id": "PROTECTED_EXACT", "lane_version": "1.0.0", "depth": 12},
        ],
        "fusion": {"method_id": "ROUND_ROBIN_DEDUP", "method_version": "1.0.0"},
    }
    assert domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(BASE)) == POLICY_DIGEST
    content = domain_digest(
        "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(BASE.model_dump(mode="python"))
    )
    assert content == CONTENT_DIGEST


def test_the_order_of_the_lanes_is_part_of_the_policy() -> None:
    swapped = policy(("PROTECTED_EXACT", 12), ("GROUPED_BM25", 3))
    assert domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(swapped)) != POLICY_DIGEST


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"lanes": []},
        {"lanes": lanes(("GROUPED_BM25", 3))},
        {
            "lanes": lanes(
                ("GROUPED_BM25", 1),
                ("PROTECTED_EXACT", 1),
                ("CHAR_BIGRAM", 1),
                ("LEXICAL_SUBSTRING_V1", 1),
                ("GROUPED_BM25", 2),
            )
        },
        {"lanes": lanes(("GROUPED_BM25", 3), ("GROUPED_BM25", 4))},
        {"lanes": lanes(("GROUPED_BM25", 0), ("PROTECTED_EXACT", 3))},
        {"lanes": lanes(("GROUPED_BM25", 161), ("PROTECTED_EXACT", 3))},
        {"lanes": lanes(("NOT_A_LANE", 3), ("PROTECTED_EXACT", 3))},
        {
            "lanes": [
                {"lane_id": "GROUPED_BM25", "depth": 3, "lane_version": "2.0.0"},
                {"lane_id": "PROTECTED_EXACT", "depth": 3},
            ]
        },
        {
            "lanes": lanes(("GROUPED_BM25", 3), ("PROTECTED_EXACT", 3)),
            "fusion": {"method_id": "RRF"},
        },
        {"lanes": lanes(("GROUPED_BM25", 3), ("PROTECTED_EXACT", 3)), "max_spans": 161},
    ],
)
def test_a_fused_policy_needs_two_to_four_distinct_known_lanes_with_a_depth(
    content: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RetrievalFusionPolicy.model_validate(content)


def test_four_distinct_lanes_are_allowed_and_the_original_lane_may_be_one_of_them() -> None:
    four = policy(
        ("LEXICAL_SUBSTRING_V1", 5), ("GROUPED_BM25", 2), ("PROTECTED_EXACT", 5), ("CHAR_BIGRAM", 5)
    )
    assert len(four.lanes) == 4


def test_the_discriminator_picks_4_0_0_and_the_other_versions_are_not_disturbed() -> None:
    content = BASE.model_dump(mode="json")
    assert type(POLICY.validate_python(content)) is RetrievalFusionPolicy
    assert type(POLICY.validate_python(BASE)) is RetrievalFusionPolicy
    assert type(POLICY.validate_python({"kind": "RETRIEVAL_POLICY", "version": "3.0.0"})) is (
        RetrievalLanePolicy
    )
    assert type(POLICY.validate_python({"kind": "RETRIEVAL_POLICY"})) is RetrievalBehaviorPolicy
    with pytest.raises(ValidationError):
        POLICY.validate_python({"kind": "RETRIEVAL_POLICY", "version": "4.0.1"})
    # A blob that says 4.0.0 but has no lanes is not read as an older version.
    with pytest.raises(ValidationError):
        POLICY.validate_python({"kind": "RETRIEVAL_POLICY", "version": "4.0.0"})


def test_one_function_says_which_policies_are_retrieval_policies() -> None:
    for retrieval in (RetrievalBehaviorPolicy(), RetrievalLanePolicy(), BASE):
        assert is_retrieval_policy(retrieval)
    assert not is_retrieval_policy(object()) and not is_retrieval_policy(None)


def test_an_evaluation_program_and_json_round_trip_keep_the_fused_policy() -> None:
    program = BehaviorEvaluationProgram(policy=BASE)
    again = BehaviorEvaluationProgram.model_validate_json(program.model_dump_json())
    assert again.policy == BASE and type(again.policy) is RetrievalFusionPolicy


def test_the_4_0_0_handler_compiles_only_4_0_0_content_and_has_no_default_or_proposal() -> None:
    registry = default_behavior_components()
    v4, v3 = registry.resolve(KIND, "4.0.0"), registry.resolve(KIND, "3.0.0")
    content = cast(dict[str, object], BASE.model_dump(mode="json"))
    assert v4.compile(content) == BASE
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        v3.compile(content)
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        v4.compile({**content, "version": "3.0.0"})
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        v4.default_policy()
    assert v4.propose(BASE, "SEMANTIC") == ()
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_KIND_MISMATCH"):
        v4.evaluate(RetrievalLanePolicy(), {"problem": "q", "evidence": []})


class EmptyStore:
    """A store with no artifact and no registry pointer: every baseline is the built-in default."""

    def list(self, project_id: str, kind: object = None) -> tuple[object, ...]:
        return ()

    def read_registry(self, project_id: str, kind: object) -> None:
        return None


def test_the_paths_that_enumerate_handlers_do_not_break_with_4_0_0_registered() -> None:
    # default_policy() has one production caller: the baseline of BehaviorSnapshotService, which
    # asks every component kind for its 2.0.0 handler. Nothing enumerates the handlers by
    # version, so a handler that refuses a default (4.0.0) is never asked for one.
    registry = default_behavior_components()
    service = BehaviorSnapshotService(cast(BehaviorArtifactStorePort, EmptyStore()), registry)
    snapshots = service.capture("project:p")
    assert {s.component for s in snapshots} == set(registry.components())
    assert all(s.policy.version == "2.0.0" for s in snapshots)
    retrieval = next(s for s in snapshots if s.component == KIND)
    assert retrieval.policy == RetrievalBehaviorPolicy()
    assert registry.components() == tuple(
        sorted(
            {
                BehaviorArtifactKind.PROMPT_BUNDLE,
                KIND,
                BehaviorArtifactKind.WORKFLOW_DEFINITION,
            }
        )
    )
