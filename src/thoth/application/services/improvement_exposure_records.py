"""Pure exposure record fields, seals and projections.

Authority, evidence/currentness, expiry, budgets, ID/clock reads, transactions and baseline
publication remain in ImprovementExposureService. Inputs are already read typed values.
"""

from __future__ import annotations

from thoth.domain.behavior_artifact import BehaviorArtifact, BehaviorArtifactKind
from thoth.domain.behavior_execution import (
    BehaviorBaselineRevision,
    BehaviorExposureRecord,
    BehaviorExposureSpec,
    BehaviorStageEvidence,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import PairedRunRecord, sealed_payload


def exposure_projection(record: BehaviorExposureRecord) -> dict[str, object]:
    return {
        "improvement_revision_id": record.spec.improvement_ref,
        "evaluation_plan_id": record.spec.evaluation_plan_ref,
        "requested_exposure_state": record.spec.stage,
        "runtime_record_digest": record.record_digest,
        "runtime_spec": record.spec.model_dump(mode="python"),
        "approval_target_digest": record.spec.spec_digest,
        "authorization_required": record.spec.stage == "CANARY",
        "stage_evidence": record.stage_evidence,
        "actual_execution_observed": bool(record.observations),
        "decision_exposure_observed": any(item.decision_exposed for item in record.observations),
        "output_authoritative": False,
    }


def transition_record(
    old: BehaviorExposureRecord, changes: dict[str, object]
) -> BehaviorExposureRecord:
    payload = old.model_dump(mode="python", exclude={"record_digest"})
    payload.update(changes)
    payload["revision"] = old.revision + 1
    record = BehaviorExposureRecord.model_validate(
        sealed_payload("BEHAVIOR_EXPOSURE_RECORD", "record_digest", payload)
    )
    return record


def exposure_spec_fields(
    project: str,
    component: BehaviorArtifactKind,
    environment: str,
    pair: PairedRunRecord,
    result_digest: str,
    stage: str,
    proposal: ControlRecord,
    plan: ControlRecord,
    artifact: BehaviorArtifact,
    target: str | None,
    workstream: str | None,
    policy_digest: str,
) -> dict[str, object]:
    return {
        "project_id": project,
        "component": component,
        "environment": environment,
        "evaluation_contract_ref": pair.spec.binding_id,
        "scope_digest": domain_digest(
            "BEHAVIOR_OVERLAP_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": project,
                    "component": component,
                    "environment": environment,
                    "evaluation_contract_ref": pair.spec.binding_id,
                }
            ),
        ),
        "stage": stage,
        "improvement_ref": proposal.record_id,
        "improvement_digest": proposal.record_digest,
        "evaluation_plan_ref": plan.record_id,
        "pair_id": pair.spec.pair_id,
        "pair_result_digest": result_digest,
        "baseline_artifact_ref": pair.spec.baseline_ref,
        "baseline_digest": pair.spec.baseline_digest,
        "candidate_artifact_ref": artifact.artifact_id,
        "candidate_digest": artifact.content_digest,
        "candidate_revision_digest": pair.spec.candidate_digest,
        "target_thread_id": target,
        "target_workstream": workstream,
        "policy_digest": policy_digest,
    }


def prepared_record(spec: BehaviorExposureSpec, pair: PairedRunRecord) -> BehaviorExposureRecord:
    assert pair.result is not None
    evidence = (
        BehaviorStageEvidence(
            stage="OFFLINE",
            status="EXECUTED",
            execution_refs=(pair.result.baseline.execution_id, pair.result.candidate.execution_id),
            receipt_refs=(pair.result.result_digest,),
        ),
        BehaviorStageEvidence(
            stage="SANDBOX",
            status="SKIPPED",
            reason_code="TRUSTED_DECLARATIVE_POLICY_NO_UNTRUSTED_CODE",
        ),
        *(
            (
                BehaviorStageEvidence(
                    stage="SHADOW",
                    status="SKIPPED",
                    reason_code="POLICY_ONLY_COMPARISON_NO_MODEL_QUALITY_CLAIM",
                ),
            )
            if spec.stage == "CANARY"
            else ()
        ),
        BehaviorStageEvidence(stage=spec.stage, status="PENDING"),
    )
    return BehaviorExposureRecord.model_validate(
        sealed_payload(
            "BEHAVIOR_EXPOSURE_RECORD",
            "record_digest",
            {
                "spec": spec,
                "revision": 1,
                "state": "PREPARED",
                "approval": None,
                "started_at": None,
                "expires_at": None,
                "request_count": 0,
                "model_call_count": 0,
                "reserved_cost_microunits": 0,
                "observations": (),
                "stage_evidence": evidence,
                "reason_code": None,
            },
        )
    )


def completion_payload(plan: ControlRecord, completed: BehaviorExposureRecord) -> dict[str, object]:
    return {
        **plan.payload,
        "evaluation_revision": plan.version + 1,
        "promotion_eligible": True,
        "runtime_exposure_ref": completed.spec.exposure_id,
        "runtime_exposure_digest": completed.record_digest,
        "runtime_observation_refs": tuple(item.execution_ref for item in completed.observations),
        "runtime_observation_digests": tuple(
            item.execution_digest for item in completed.observations
        ),
        "exposure_state": "CANARY",
    }


def baseline_scope_digest(spec: BehaviorExposureSpec) -> str:
    return domain_digest(
        "BEHAVIOR_BASELINE_SCOPE",
        "1.0.0",
        canonical_payload(
            {
                "project_id": spec.project_id,
                "component": spec.component,
                "environment": spec.environment,
                "target_thread_id": spec.target_thread_id,
                "target_workstream": spec.target_workstream,
            }
        ),
    )


def rollback_baseline_fields(
    spec: BehaviorExposureSpec, scope_digest: str, current: BehaviorBaselineRevision, actor: str
) -> dict[str, object]:
    return {
        "project_id": spec.project_id,
        "component": spec.component,
        "environment": spec.environment,
        "scope_digest": scope_digest,
        "target_thread_id": spec.target_thread_id,
        "target_workstream": spec.target_workstream,
        "revision": current.revision + 1,
        "change_kind": "ROLLED_BACK",
        "artifact_ref": spec.baseline_artifact_ref,
        "content_digest": spec.baseline_digest,
        "previous_digest": current.content_digest,
        "exposure_ref": spec.exposure_id,
        "approval_ref": "exposure-spec:" + spec.spec_digest,
        "actor_ref": actor,
    }


def promotion_evidence_projection(
    identifier: str, record: BehaviorExposureRecord
) -> dict[str, object]:
    return {
        "runtime_exposure_ref": identifier,
        "runtime_exposure_digest": record.record_digest,
        "runtime_scope": record.spec.model_dump(mode="python"),
        "baseline_kind": "BEHAVIOR",
        "scientific_baseline_change": False,
    }


def promoted_baseline_fields(
    spec: BehaviorExposureSpec,
    scope_digest: str,
    revision: int,
    promotion: ControlRecord,
    actor: str,
) -> dict[str, object]:
    return {
        "project_id": spec.project_id,
        "component": spec.component,
        "environment": spec.environment,
        "scope_digest": scope_digest,
        "target_thread_id": spec.target_thread_id,
        "target_workstream": spec.target_workstream,
        "revision": revision,
        "change_kind": "PROMOTED",
        "artifact_ref": spec.candidate_artifact_ref,
        "content_digest": spec.candidate_digest,
        "previous_digest": spec.baseline_digest,
        "exposure_ref": spec.exposure_id,
        "approval_ref": promotion.control_revision_id,
        "actor_ref": actor,
    }
