"""Pure exposure builders: seals, evidence vocabulary, budget validation and projection binding."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from thoth.application.services import improvement_exposure_records as records
from thoth.domain.behavior_execution import (
    BehaviorBaselineRevision,
    BehaviorExposureRecord,
    BehaviorExposureSpec,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import PairedRunRecord

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/improvement_exposure_golden.json"


def output(scenario: str, method: str, *, state: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name, value in data[scenario]["outputs"]:
        if name != method or not isinstance(value, dict) or "error" in value:
            continue
        fields = cast(dict[str, Any], value)
        if state is None or fields.get("state") == state:
            return fields
    raise AssertionError((scenario, method, state))


def prepared(stage: str = "CANARY") -> BehaviorExposureRecord:
    return BehaviorExposureRecord.model_validate(output("prepare_" + stage.lower(), "prepare"))


def completed() -> BehaviorExposureRecord:
    return BehaviorExposureRecord.model_validate(
        output("complete_manual", "complete", state="COMPLETED")
    )


def pair() -> PairedRunRecord:
    return PairedRunRecord.model_validate(output("promote_rollback", "prepare_promotion"))


def test_transition_reseals_without_mutating_old_snapshot() -> None:
    old = completed()
    old_json = old.model_dump_json()
    revised = records.transition_record(old, {"state": "ROLLED_BACK", "reason_code": "UNIT"})
    assert old.model_dump_json() == old_json
    assert revised.revision == old.revision + 1
    assert revised.spec == old.spec and revised.observations == old.observations
    assert revised.record_digest == domain_digest(
        "BEHAVIOR_EXPOSURE_RECORD",
        "1.0.0",
        canonical_payload(revised.model_dump(mode="python", exclude={"record_digest"})),
    )
    assert revised.record_digest != old.record_digest


@pytest.mark.parametrize(
    "counter", ["request_count", "model_call_count", "reserved_cost_microunits"]
)
def test_transition_does_not_bypass_typed_budget_limits(counter: str) -> None:
    old = completed()
    maximum = {
        "request_count": old.spec.max_requests,
        "model_call_count": old.spec.max_model_calls,
        "reserved_cost_microunits": old.spec.max_billed_cost_microunits,
    }[counter]
    with pytest.raises(ValidationError):
        records.transition_record(old, {counter: maximum + 1})


@pytest.mark.parametrize("stage", ["SHADOW", "CANARY"])
def test_prepared_record_distinguishes_measured_and_pending_stages(stage: str) -> None:
    seed = prepared(stage)
    built = records.prepared_record(seed.spec, pair())
    stages = [(e.stage, e.status) for e in built.stage_evidence]
    expected = [("OFFLINE", "EXECUTED"), ("SANDBOX", "SKIPPED")]
    if stage == "CANARY":
        expected.append(("SHADOW", "SKIPPED"))
    expected.append((stage, "PENDING"))
    assert stages == expected
    assert built.state == "PREPARED" and built.revision == 1
    assert built.observations == () and built.request_count == built.model_call_count == 0
    assert built.approval is None and built.started_at is None
    assert built.spec == seed.spec


@pytest.mark.parametrize("observed", [False, True])
def test_projection_binds_runtime_record_without_claiming_authority(observed: bool) -> None:
    record = completed() if observed else prepared()
    projection = records.exposure_projection(record)
    assert projection["runtime_record_digest"] == record.record_digest
    assert projection["runtime_spec"] == record.spec.model_dump(mode="python")
    assert projection["approval_target_digest"] == record.spec.spec_digest
    assert projection["requested_exposure_state"] == record.spec.stage
    assert projection["actual_execution_observed"] is observed
    assert projection["decision_exposure_observed"] is observed
    assert projection["output_authoritative"] is False


def test_spec_fields_exclude_id_clock_and_budget_owners() -> None:
    spec = prepared().spec
    # Value doubles for already-read metadata; helpers must not reach services or clocks.
    proposal: Any = SimpleNamespace(
        record_id=spec.improvement_ref, record_digest=spec.improvement_digest
    )
    plan: Any = SimpleNamespace(record_id=spec.evaluation_plan_ref)
    artifact: Any = SimpleNamespace(
        artifact_id=spec.candidate_artifact_ref, content_digest=spec.candidate_digest
    )
    fields = records.exposure_spec_fields(
        spec.project_id,
        spec.component,
        spec.environment,
        pair(),
        spec.pair_result_digest,
        spec.stage,
        proposal,
        plan,
        artifact,
        spec.target_thread_id,
        spec.target_workstream,
        spec.policy_digest,
    )
    assert not set(fields) & {
        "exposure_id",
        "created_at",
        "duration_seconds",
        "max_requests",
        "max_model_calls",
        "max_billed_cost_microunits",
        "comparison_scope",
        "model_quality",
    }
    assert fields["policy_digest"] == spec.policy_digest
    assert fields["pair_result_digest"] == spec.pair_result_digest
    assert fields["candidate_artifact_ref"] == spec.candidate_artifact_ref
    assert fields["scope_digest"] == domain_digest(
        "BEHAVIOR_OVERLAP_SCOPE",
        "1.0.0",
        canonical_payload(
            {
                "project_id": spec.project_id,
                "component": spec.component,
                "environment": spec.environment,
                "evaluation_contract_ref": pair().spec.binding_id,
            }
        ),
    )


def test_completion_payload_binds_observations_and_preserves_prior_fields() -> None:
    record = completed()
    prior: dict[str, object] = {"kept": "value", "evaluation_revision": 1}
    plan: Any = SimpleNamespace(payload=prior, version=2)
    payload = records.completion_payload(plan, record)
    assert prior == {"kept": "value", "evaluation_revision": 1}
    assert payload["kept"] == "value" and payload["evaluation_revision"] == 3
    assert payload["promotion_eligible"] is True
    assert payload["runtime_exposure_ref"] == record.spec.exposure_id
    assert payload["runtime_exposure_digest"] == record.record_digest
    assert payload["runtime_observation_refs"] == tuple(
        x.execution_ref for x in record.observations
    )
    assert payload["runtime_observation_digests"] == tuple(
        x.execution_digest for x in record.observations
    )


def test_baseline_drafts_keep_scope_and_clock_ownership_separate() -> None:
    spec: BehaviorExposureSpec = prepared().spec
    published = BehaviorBaselineRevision.model_validate(
        output("promote_rollback", "apply_promotion")
    )
    scope = records.baseline_scope_digest(spec)
    assert scope == published.scope_digest
    assert scope != spec.scope_digest  # baseline target scope differs from overlap scope.
    promotion: Any = SimpleNamespace(control_revision_id="approval:unit")
    promoted = records.promoted_baseline_fields(spec, scope, 2, promotion, "human:unit")
    restored = records.rollback_baseline_fields(spec, scope, published, "human:unit")
    assert "created_at" not in promoted and "created_at" not in restored
    assert promoted["change_kind"] == "PROMOTED" and restored["change_kind"] == "ROLLED_BACK"
    assert promoted["artifact_ref"] == spec.candidate_artifact_ref
    assert restored["artifact_ref"] == spec.baseline_artifact_ref
    assert restored["revision"] == published.revision + 1
    assert restored["previous_digest"] == published.content_digest
    assert promoted["approval_ref"] == "approval:unit"


def test_promotion_projection_is_behavior_evidence_not_scientific_approval() -> None:
    record = completed()
    payload = records.promotion_evidence_projection(record.spec.exposure_id, record)
    assert payload["runtime_exposure_digest"] == record.record_digest
    assert payload["runtime_scope"] == record.spec.model_dump(mode="python")
    assert payload["baseline_kind"] == "BEHAVIOR"
    assert payload["scientific_baseline_change"] is False
