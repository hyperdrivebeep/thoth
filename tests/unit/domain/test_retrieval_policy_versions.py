"""S3: retrieval policy 3.0.0, the (kind, version) discriminator and the 2.0.0 compatibility."""

from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    PromptBehaviorPolicy,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
    WorkflowBehaviorPolicy,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import BehaviorEvaluationProgram, sealed_payload

type AnyPolicy = (
    PromptBehaviorPolicy | RetrievalBehaviorPolicy | RetrievalLanePolicy | WorkflowBehaviorPolicy
)
POLICY: TypeAdapter[AnyPolicy] = cast(TypeAdapter[AnyPolicy], TypeAdapter(BehaviorPolicy))
SHA = "a" * 64

# Digests of the 2.0.0 policy, taken before retrieval policy 3.0.0 existed. They must not move.
DEFAULT_2_0_0_POLICY_DIGEST = "237453b05cfb5a7f1982725db7b08b34a856e57225741d33f7191ca3d8ace85d"
DEFAULT_2_0_0_CONTENT_DIGEST = "af04aacf42bb7a2d0cdd637f9e6f8680d9bac394f41860080bad433dfa287719"
SMALL_2_0_0_POLICY_DIGEST = "f0fb99b0169b006cb98080e4fb3c1f5ca22d8af817c6aa6688bf902843ca576b"
SMALL_2_0_0_CONTENT_DIGEST = "8275c8f74c82f07b7af4b794b2200b89b01e42c72ababae7a872c06197ca18d7"
PROMPT_CONTENT_DIGEST = "d19e765252bc479f71ce7ea3e3966a2a4acd652379bbac5813cb05b27122ad5d"
WORKFLOW_CONTENT_DIGEST = "52a336ecab1ffea6f14f816b10461853ae31b6e883f7c0851ac8caf057552ff9"


def test_the_2_0_0_policy_is_unchanged_down_to_its_digests() -> None:
    default, small = (
        RetrievalBehaviorPolicy(),
        RetrievalBehaviorPolicy(max_spans=1, character_budget=100),
    )
    assert default.model_dump() == {
        "kind": "RETRIEVAL_POLICY",
        "version": "2.0.0",
        "max_spans": 160,
        "character_budget": 50000,
    }
    assert domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(default)) == (
        DEFAULT_2_0_0_POLICY_DIGEST
    )
    assert domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(small)) == (
        SMALL_2_0_0_POLICY_DIGEST
    )
    content = "BEHAVIOR_ARTIFACT_CONTENT"
    for policy, digest in (
        (default, DEFAULT_2_0_0_CONTENT_DIGEST),
        (small, SMALL_2_0_0_CONTENT_DIGEST),
        (PromptBehaviorPolicy(), PROMPT_CONTENT_DIGEST),
        (WorkflowBehaviorPolicy(), WORKFLOW_CONTENT_DIGEST),
    ):
        assert (
            domain_digest(content, "1.0.0", canonical_payload(policy.model_dump(mode="python")))
            == digest
        )


def test_policy_3_0_0_adds_the_lane_and_keeps_the_budgets() -> None:
    policy = RetrievalLanePolicy()
    assert policy.model_dump(mode="json") == {
        "kind": "RETRIEVAL_POLICY",
        "version": "3.0.0",
        "max_spans": 160,
        "character_budget": 50000,
        "lanes": [{"lane_id": "LEXICAL_SUBSTRING_V1", "lane_version": "1.0.0"}],
    }
    assert domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(policy)) != (
        DEFAULT_2_0_0_POLICY_DIGEST
    )


@pytest.mark.parametrize(
    "lanes",
    [[], [{}, {}], [{"lane_id": "PROTECTED_EXACT"}], [{"lane_version": "2.0.0"}]],
)
def test_policy_3_0_0_accepts_only_the_one_lane_that_exists(lanes: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        RetrievalLanePolicy.model_validate({"lanes": lanes})


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        ({"kind": "RETRIEVAL_POLICY", "version": "2.0.0"}, RetrievalBehaviorPolicy),
        ({"kind": "RETRIEVAL_POLICY", "version": "3.0.0"}, RetrievalLanePolicy),
        ({"kind": "PROMPT_BUNDLE", "version": "2.0.0"}, PromptBehaviorPolicy),
        ({"kind": "WORKFLOW_DEFINITION", "version": "2.0.0"}, WorkflowBehaviorPolicy),
    ],
)
def test_the_discriminator_picks_the_class_by_kind_and_version(
    blob: dict[str, Any], expected: type
) -> None:
    assert type(POLICY.validate_python(blob)) is expected
    assert type(POLICY.validate_python(expected())) is expected


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        ({"kind": "RETRIEVAL_POLICY"}, RetrievalBehaviorPolicy),
        ({"kind": "RETRIEVAL_POLICY", "max_spans": 7}, RetrievalBehaviorPolicy),
        ({"kind": "PROMPT_BUNDLE"}, PromptBehaviorPolicy),
        ({"kind": "WORKFLOW_DEFINITION"}, WorkflowBehaviorPolicy),
    ],
)
def test_a_stored_policy_without_a_version_key_is_read_as_2_0_0(
    blob: dict[str, Any], expected: type
) -> None:
    # Older stored data may omit the version; the union used to accept it through the field default.
    parsed = POLICY.validate_python(blob)
    assert type(parsed) is expected and parsed.version == "2.0.0"


@pytest.mark.parametrize(
    "blob",
    [
        {"kind": "RETRIEVAL_POLICY", "version": "9.9.9"},
        {"kind": "RETRIEVAL_POLICY", "version": "2.0.1"},
        {"kind": "PROMPT_BUNDLE", "version": "3.0.0"},
        {"version": "2.0.0"},
        {"kind": "NOT_A_KIND", "version": "2.0.0"},
        {},
    ],
)
def test_an_unknown_kind_or_version_is_refused(blob: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        POLICY.validate_python(blob)


def snapshot(policy: object, component: str = "RETRIEVAL_POLICY") -> dict[str, object]:
    return sealed_payload(
        "ACTIVE_BEHAVIOR_SNAPSHOT",
        "snapshot_digest",
        {
            "project_id": "p",
            "component": component,
            "environment": "LOCAL",
            "artifact_ref": None,
            "content_digest": SHA,
            "policy": policy,
            "origin": "BUILTIN_DEFAULT",
            "registry_revision": 0,
            "exposure_ref": None,
            "ignored_registry_digest": None,
            "reason_code": None,
        },
    )


def test_snapshots_of_both_versions_round_trip_through_json() -> None:
    for policy in (RetrievalBehaviorPolicy(max_spans=5), RetrievalLanePolicy(max_spans=5)):
        made = ActiveBehaviorSnapshot.model_validate(snapshot(policy))
        again = ActiveBehaviorSnapshot.model_validate_json(made.model_dump_json())
        assert again == made and type(again.policy) is type(policy)


def test_a_stored_2_0_0_snapshot_whose_policy_has_no_version_still_parses() -> None:
    made = ActiveBehaviorSnapshot.model_validate(snapshot(RetrievalBehaviorPolicy(max_spans=5)))
    raw: dict[str, Any] = made.model_dump(mode="json")
    del raw["policy"]["version"]
    again = ActiveBehaviorSnapshot.model_validate(raw)
    assert type(again.policy) is RetrievalBehaviorPolicy and again.policy.max_spans == 5
    assert again.snapshot_digest == made.snapshot_digest


def test_an_evaluation_program_can_carry_a_3_0_0_policy() -> None:
    program = BehaviorEvaluationProgram(policy=RetrievalLanePolicy())
    again = BehaviorEvaluationProgram.model_validate_json(program.model_dump_json())
    assert type(again.policy) is RetrievalLanePolicy


def test_the_3_0_0_handler_compiles_only_3_0_0_content() -> None:
    registry = default_behavior_components()
    kind = BehaviorArtifactKind.RETRIEVAL_POLICY
    v3, v2 = registry.resolve(kind, "3.0.0"), registry.resolve(kind, "2.0.0")
    content: dict[str, object] = {"kind": "RETRIEVAL_POLICY", "version": "3.0.0", "max_spans": 9}
    assert v3.compile(content) == RetrievalLanePolicy(max_spans=9)
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        v2.compile(content)
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        v3.compile({**content, "version": "2.0.0"})
    assert v2.compile({"kind": "RETRIEVAL_POLICY"}) == RetrievalBehaviorPolicy()
