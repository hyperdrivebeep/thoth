"""Pure recursive improvement records: digest binding and non-authoritative projections."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.unit.improvement.test_evaluation_integrity import prepared_request

from thoth.application.services import recursive_improvement_records as records
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.improvement import RecursiveImprovementResult

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/recursive_improvement_golden.json"


def result(scenario: str, state: str) -> RecursiveImprovementResult:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for method, item in data[scenario]["outputs"]:
        if method == "observe" and isinstance(item, dict):
            values = cast(dict[str, Any], item)
            if values.get("state") == state:
                return RecursiveImprovementResult.model_validate(values)
    raise AssertionError((scenario, state))


def check_receipt(value: RecursiveImprovementResult) -> None:
    assert value.receipt_digest == domain_digest(
        "RECURSIVE_IMPROVEMENT_RECEIPT",
        "1.0.0",
        canonical_payload(value.model_dump(mode="python", exclude={"receipt_digest"})),
    )


@pytest.mark.parametrize(
    "projection,reason",
    [
        (None, "NO_EXECUTION"),
        ({"terminal_state": "COMPLETED"}, "NO_FAILURE"),
        ({"terminal_state": "HOLD"}, "NO_QUALIFYING_FAILURE_EVENT"),
    ],
)
def test_no_result_does_not_claim_a_completed_evaluation(
    projection: dict[str, object] | None, reason: str
) -> None:
    observation = records.untriggered_observation(projection)
    assert observation.trigger_state == "NOT_TRIGGERED" and observation.reason_code == reason


@pytest.mark.parametrize(
    "scenario,state,trigger",
    [
        ("legacy_success", "BELOW_THRESHOLD", "OBSERVED"),
        ("legacy_success", "PROMOTED_LOCAL", "EVALUATED"),
        ("legacy_unavailable", "HELD", "HELD"),
    ],
)
def test_observation_links_the_given_result_and_event(
    scenario: str, state: str, trigger: str
) -> None:
    value = result(scenario, state)
    observation = records.result_observation(value, "a" * 64)
    assert observation.trigger_state == trigger and observation.reason_code == state
    assert observation.failure_observation_ref == "failure:" + "a" * 64
    assert observation.result_ref == value.receipt_digest
    assert records.result_observation(value, None).failure_observation_ref is None


def test_failure_fingerprint_normalizes_detail_but_preserves_action_identity() -> None:
    a = records.failure_fingerprint("SEMANTIC", "  Mixed Failure  ", {"action_id": "a"})
    assert a == records.failure_fingerprint("SEMANTIC", "mixed failure", {"action_id": "a"})
    assert a != records.failure_fingerprint("SEMANTIC", "mixed failure", {"action_id": "b"})
    assert a != records.failure_fingerprint("CODE", "mixed failure", {"action_id": "a"})


def test_candidate_and_fixture_digests_bind_the_failure_without_exposing_expected_values() -> None:
    draft, digest = records.legacy_candidate("a" * 64, "SEMANTIC")
    assert digest == domain_digest("IMPROVEMENT_CANDIDATE", "1.0.0", canonical_payload(draft))
    assert records.legacy_candidate("b" * 64, "SEMANTIC")[1] != digest
    fixture, hidden = records.fixture_digests("a" * 64)
    assert fixture != hidden and len(fixture) == len(hidden) == 64
    assert (fixture, hidden) != records.fixture_digests("b" * 64)


def test_request_seal_matches_the_existing_direct_request_fixture() -> None:
    request = prepared_request()
    draft = request.model_dump(mode="python", exclude={"request_digest"})
    assert records.seal_evaluation_request(draft) == request


@pytest.mark.parametrize("mode", ["none", "no_result", "measured"])
def test_pair_link_reseals_and_only_claims_measured_provenance_with_result(mode: str) -> None:
    old = result("legacy_success", "BELOW_THRESHOLD")
    before = old.model_dump_json()
    pair: Any = (
        None
        if mode == "none"
        else SimpleNamespace(
            spec=SimpleNamespace(pair_id="pair:unit"),
            result=SimpleNamespace(result_digest="b" * 64) if mode == "measured" else None,
        )
    )
    new = records.paired_result(old, pair)
    assert old.model_dump_json() == before
    assert new.paired_evaluation_id == (None if mode == "none" else "pair:unit")
    assert new.paired_result_digest == ("b" * 64 if mode == "measured" else None)
    assert new.evaluation_provenance == ("MEASURED_PAIR" if mode == "measured" else None)
    check_receipt(new)


def test_behavior_link_preserves_input_and_reseals_the_registry_identity() -> None:
    old = result("legacy_success", "BELOW_THRESHOLD")
    before = old.model_dump_json()
    artifact: Any = SimpleNamespace(
        artifact_id="artifact:unit", kind=BehaviorArtifactKind.WORKFLOW_DEFINITION
    )
    registry: Any = SimpleNamespace(active_digest="c" * 64)
    new = records.behavior_result(old, artifact, registry)
    assert old.model_dump_json() == before
    assert new.behavior_artifact_id == "artifact:unit"
    assert new.behavior_artifact_kind == "WORKFLOW_DEFINITION"
    assert new.behavior_registry_active_digest == "c" * 64
    assert new.semantic_truth_certified is False
    check_receipt(new)


def test_result_fields_leave_id_creation_with_caller_and_rebuild_original_result() -> None:
    seed = result("legacy_success", "BELOW_THRESHOLD")
    fields = records.result_fields(
        seed.project_id,
        seed.thread_id,
        seed.state,
        seed.failure_fingerprint,
        seed.failure_count,
        seed.baseline_digest,
        seed.candidate_digest,
        seed.fixture_digest,
        seed.evaluator_id,
        seed.evaluation,
        seed.exposures,
        seed.rollback_reason,
        seed.active_digest,
        seed.exact_digest_canary,
        seed.created_at,
    )
    assert "run_id" not in fields
    assert all(
        fields[name] is False
        for name in (
            "official_kpi_changed",
            "safety_threshold_changed",
            "waiver_changed",
            "r4_decision_changed",
            "model_weights_changed",
            "semantic_truth_certified",
        )
    )
    assert records.seal_result({"run_id": seed.run_id, **fields}) == seed


def test_legacy_exposure_seal_preserves_test_only_stage_and_non_effect_flags() -> None:
    seed = result("legacy_success", "PROMOTED_LOCAL").exposures[0]
    draft: dict[str, object] = {
        "exposure_id": seed.exposure_id,
        "stage": seed.stage,
        "project_id": seed.project_id,
        "baseline_digest": seed.baseline_digest,
        "candidate_digest": seed.candidate_digest,
        "fixture_digest": seed.fixture_digest,
        "evaluator_id": seed.evaluator_id,
        "exact_candidate_digest": seed.exact_candidate_digest,
        "hidden_holdout_accessed": seed.hidden_holdout_accessed,
        "external_effect": seed.external_effect,
        "recorded_at": seed.recorded_at,
    }
    rebuilt = records.seal_exposure(draft)
    assert rebuilt == seed and rebuilt.external_effect is False
    assert rebuilt.hidden_holdout_accessed is False


def test_failure_payload_keeps_trigger_identity_without_allocating_a_new_id() -> None:
    payload = records.failure_observation_payload(
        "thread", "fingerprint", "SEMANTIC", "detail", "a" * 64, 2
    )
    assert payload["thread_id"] == "thread" and payload["ordinal"] == 2
    assert payload["failure_fingerprint"] == "fingerprint"
    assert not set(payload) & {"record_id", "created_at", "request_id"}
