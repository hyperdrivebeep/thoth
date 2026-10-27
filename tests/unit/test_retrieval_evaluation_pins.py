"""G3 1b: the evaluation input, the evaluation outputs and the comparison identity of the existing
retrieval policies (2.0.0, 3.0.0 with the original lane, 3.0.0 with PROTECTED_EXACT) do not move.

The digests were taken from the code as it stood before structure groups existed. A case that names
no groups serializes exactly as before: the optional field is not written when it is empty.
"""

from collections.abc import Mapping

from pydantic import JsonValue
from tests.unit.test_retrieval_selection_oracle import span

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.domain.behavior_policy import (
    RetrievalBehaviorInput,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import EvaluationCasePack, sealed_payload
from thoth.domain.retrieval_lane import RETRIEVAL_EVALUATOR_ID

FILLER = " with enough words around it to be an ordinary line"
SPANS = (
    span("a1", "release v2.5 of the tool" + FILLER, line=1),
    span("a2", "build 3 of the tool" + FILLER, line=2),
    span("a3", "gcc 16.0 on aarch64 passes" + FILLER, line=3),
)
PAYLOAD: dict[str, JsonValue] = {
    "problem": "v2.5 gcc에서 build",
    "evidence": [s.model_dump(mode="json") for s in SPANS],
    "queries": ["tool release"],
    "pinned_refs": ["span:a3"],
    "catalog": {
        "ordered_refs": [s.span_id for s in SPANS],
        "protected_refs": ["span:a1"],
        "all_observed": False,
        "structure_count": 0,
    },
}
POLICIES = {
    "2.0.0": RetrievalBehaviorPolicy(max_spans=2),
    "3.0.0 original lane": RetrievalLanePolicy(max_spans=2),
    "3.0.0 PROTECTED_EXACT": RetrievalLanePolicy.model_validate(
        {"max_spans": 2, "lanes": [{"lane_id": "PROTECTED_EXACT"}]}
    ),
}
OUTPUT_DIGESTS = {
    "2.0.0": "f8ba1951c8ab11506ad02742f749d5a19b1ad23e58de55a46a8709da19195803",
    "3.0.0 original lane": "f8ba1951c8ab11506ad02742f749d5a19b1ad23e58de55a46a8709da19195803",
    "3.0.0 PROTECTED_EXACT": "46857b2cc0218dbc8dd0be6020f5692d1b33cb90e31dd40a7583e6c33ada8efd",
}
INPUT_MODEL_DIGEST = "9cd1401903d11c604105bc09e90a65d6d020fe9955a5ccb49e23d5e516e691e8"
PUBLIC_INPUT_DIGEST = "efb94ad0b1f73ea071eae3bfc4f1f48986f959b9a279a58a0afa3e48b6da54d0"
DEDUPE_DIGEST = "8dab7613a73930d87d2c52c1bccfb5ba835cc3a374dd44fd4c053da1cb9872c5"


def digest(kind: str, value: Mapping[str, object]) -> str:
    return domain_digest(kind, "1.0.0", canonical_payload(value))


def test_the_evaluation_outputs_of_the_existing_policies_keep_their_digests() -> None:
    for name, policy in POLICIES.items():
        output = evaluate_retrieval(policy, PAYLOAD)
        assert output["evaluator"] == RETRIEVAL_EVALUATOR_ID
        assert digest("PIN_OUTPUT", output) == OUTPUT_DIGESTS[name], name


def test_an_input_that_names_no_groups_serializes_as_it_always_did() -> None:
    parsed = RetrievalBehaviorInput.model_validate(PAYLOAD)
    dumped = parsed.model_dump(mode="json")
    assert "groups" not in str(dumped)
    assert digest("PIN_INPUT", dumped) == INPUT_MODEL_DIGEST


def test_the_public_input_digest_of_a_retrieval_case_pack_keeps_its_value() -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "retrieval", "payload": PAYLOAD},
            "expected_output": {"selected_refs": []},
            "axis": "quality",
        }
    ]
    pack = EvaluationCasePack.model_validate(
        sealed_payload(
            "EVALUATION_CASE_PACK",
            "pack_digest",
            {
                "project_id": "project:pins",
                "pack_id": "pins",
                "version": "1.0.0",
                "cases": cases,
                "initial_memory": {},
                "resource_refs": (),
                "expected_visibility": "SCORER_ONLY",
                "expected_values_exposed": False,
                "max_pair_runs": 1,
            },
        )
    )
    inputs = {"cases": [case.public.model_dump(mode="python") for case in pack.cases]}
    assert digest("EVALUATION_PUBLIC_INPUTS", inputs) == PUBLIC_INPUT_DIGEST


def test_the_comparison_identity_of_a_retrieval_pair_keeps_its_digest() -> None:
    identity: dict[str, object] = {
        "project_id": "project:pins",
        "plan_id": "plan:1",
        "plan_digest": "a" * 64,
        "component": "RETRIEVAL_POLICY",
        "baseline_digest": "b" * 64,
        "candidate_digest": "c" * 64,
        "public_input_digest": PUBLIC_INPUT_DIGEST,
        "evaluator": RETRIEVAL_EVALUATOR_ID,
    }
    assert digest("EVALUATION_DEDUPE", identity) == DEDUPE_DIGEST
