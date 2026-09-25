"""Immutable behavior selection and actual consumption traces, never scientific certification."""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import BehaviorPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import check_digest
from thoth.domain.ids import Sha256


class ActiveBehaviorSnapshot(DomainModel):
    project_id: str
    component: BehaviorArtifactKind
    environment: str
    artifact_ref: str | None
    content_digest: Sha256
    policy: BehaviorPolicy
    origin: Literal["BASELINE", "BUILTIN_DEFAULT", "CANARY", "SHADOW"]
    registry_revision: int = Field(ge=0)
    exposure_ref: str | None = None
    ignored_registry_digest: Sha256 | None = None
    reason_code: str | None = None
    snapshot_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> Self:
        if self.component.value != self.policy.kind:
            raise ValueError("BEHAVIOR_COMPONENT_MISMATCH")
        check_digest(self, "snapshot_digest", "ACTIVE_BEHAVIOR_SNAPSHOT")
        return self


class BehaviorUse(DomainModel):
    component: BehaviorArtifactKind
    snapshot_digest: Sha256
    operation: Literal[
        "MODEL_DISPATCH",
        "MODEL_INPUT",
        "PROMPT_RENDER",
        "EVIDENCE_SELECTION",
        "WORKFLOW_POLICY",
        "POLICY_HELD",
    ]
    input_digest: Sha256
    output_digest: Sha256
    elapsed_ns: int = Field(ge=0)
    billed_cost_microunits: int | None = Field(default=None, ge=0)
    cost_basis: Literal["LOCAL_SCRIPTED", "NOT_BILLABLE", "UNKNOWN"]


class BehaviorExecutionRecord(DomainModel):
    execution_id: str
    project_id: str
    thread_id: str
    actor_ref: str
    session_ref: str | None
    input_head_set_digest: Sha256
    project_policy_digest: Sha256
    state: Literal["RUNNING", "COMPLETED", "HELD", "CANCELLED"]
    snapshots: tuple[ActiveBehaviorSnapshot, ...]
    uses: tuple[BehaviorUse, ...] = ()
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    reason_code: str | None = None
    semantic_truth_certified: Literal[False] = False
    receipt_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> Self:
        if any(item.project_id != self.project_id for item in self.snapshots):
            raise ValueError("BEHAVIOR_SNAPSHOT_PROJECT_MISMATCH")
        known = {item.snapshot_digest for item in self.snapshots}
        if any(item.snapshot_digest not in known for item in self.uses):
            raise ValueError("BEHAVIOR_USE_SNAPSHOT_MISSING")
        check_digest(self, "receipt_digest", "BEHAVIOR_EXECUTION_RECORD")
        return self


class BehaviorExposureSpec(DomainModel):
    exposure_id: str
    project_id: str
    component: BehaviorArtifactKind
    environment: str
    evaluation_contract_ref: str
    scope_digest: Sha256
    stage: Literal["SHADOW", "CANARY"]
    improvement_ref: str
    improvement_digest: Sha256
    evaluation_plan_ref: str
    pair_id: str
    pair_result_digest: Sha256
    baseline_artifact_ref: str
    baseline_digest: Sha256
    candidate_artifact_ref: str
    candidate_digest: Sha256
    candidate_revision_digest: Sha256
    target_thread_id: str | None = None
    target_workstream: str | None = None
    policy_digest: Sha256
    duration_seconds: int = Field(ge=1, le=3600)
    max_requests: int = Field(ge=1, le=100)
    max_model_calls: int = Field(ge=1, le=1000)
    max_billed_cost_microunits: int = Field(ge=0)
    comparison_scope: Literal["DETERMINISTIC_COMPONENT_POLICY"] = "DETERMINISTIC_COMPONENT_POLICY"
    model_quality: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"
    spec_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> Self:
        if self.target_thread_id is not None and self.target_workstream is not None:
            raise ValueError("BEHAVIOR_EXPOSURE_TARGET_AMBIGUOUS")
        expected_scope = domain_digest(
            "BEHAVIOR_OVERLAP_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": self.project_id,
                    "component": self.component,
                    "environment": self.environment,
                    "evaluation_contract_ref": self.evaluation_contract_ref,
                }
            ),
        )
        if self.scope_digest != expected_scope:
            raise ValueError("BEHAVIOR_OVERLAP_SCOPE_MISMATCH")
        check_digest(self, "spec_digest", "BEHAVIOR_EXPOSURE_SPEC")
        return self


class BehaviorApproval(DomainModel):
    actor_ref: str
    role_assignment_ref: str
    session_ref: str | None
    approved_digest: Sha256
    approved_at: AwareDatetime


class BehaviorObservation(DomainModel):
    execution_ref: str
    execution_digest: Sha256
    consumed: bool
    successful: bool
    decision_exposed: bool = False
    model_calls: int = Field(ge=0)
    billed_cost_microunits: int | None = Field(ge=0)
    reason_code: str | None


class BehaviorStageEvidence(DomainModel):
    stage: Literal["OFFLINE", "SANDBOX", "SHADOW", "CANARY"]
    status: Literal["PENDING", "RUNNING", "EXECUTED", "SKIPPED"]
    execution_refs: tuple[str, ...] = ()
    receipt_refs: tuple[str, ...] = ()
    reason_code: str | None = None

    @model_validator(mode="after")
    def requires_evidence(self) -> Self:
        if self.status == "EXECUTED" and (not self.execution_refs or not self.receipt_refs):
            raise ValueError("BEHAVIOR_STAGE_EXECUTION_EVIDENCE_REQUIRED")
        if self.status == "SKIPPED" and not self.reason_code:
            raise ValueError("BEHAVIOR_STAGE_SKIP_REASON_REQUIRED")
        return self


class BehaviorExposureRecord(DomainModel):
    spec: BehaviorExposureSpec
    revision: int = Field(ge=1)
    state: Literal[
        "PREPARED", "APPROVED", "ARMED", "ACTIVE", "COMPLETED", "REJECTED", "ROLLED_BACK", "SKIPPED"
    ]
    approval: BehaviorApproval | None = None
    started_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    request_count: int = Field(default=0, ge=0)
    model_call_count: int = Field(default=0, ge=0)
    reserved_cost_microunits: int = Field(default=0, ge=0)
    observations: tuple[BehaviorObservation, ...] = Field(default=(), max_length=100)
    stage_evidence: tuple[BehaviorStageEvidence, ...] = ()
    reason_code: str | None = None
    record_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> Self:
        if (
            self.spec.stage == "CANARY"
            and self.state in {"APPROVED", "ARMED", "ACTIVE", "COMPLETED"}
            and (self.approval is None or self.approval.approved_digest != self.spec.spec_digest)
        ):
            raise ValueError("BEHAVIOR_EXACT_APPROVAL_REQUIRED")
        if self.state in {"ARMED", "ACTIVE", "COMPLETED"} and (
            self.started_at is None or self.expires_at is None
        ):
            raise ValueError("BEHAVIOR_EXPOSURE_TIME_MISSING")
        if (
            self.request_count > self.spec.max_requests
            or self.model_call_count > self.spec.max_model_calls
        ):
            raise ValueError("BEHAVIOR_EXPOSURE_BUDGET_EXCEEDED")
        if self.reserved_cost_microunits > self.spec.max_billed_cost_microunits:
            raise ValueError("BEHAVIOR_COST_BUDGET_EXCEEDED")
        if len({item.execution_ref for item in self.observations}) != len(self.observations):
            raise ValueError("BEHAVIOR_OBSERVATION_DUPLICATED")
        started_refs = {
            ref
            for item in self.stage_evidence
            if item.status == "RUNNING"
            for ref in item.execution_refs
        }
        if any(item.execution_ref not in started_refs for item in self.observations):
            raise ValueError("BEHAVIOR_OBSERVATION_WITHOUT_RUN")
        check_digest(self, "record_digest", "BEHAVIOR_EXPOSURE_RECORD")
        return self


class BehaviorBaselineRevision(DomainModel):
    project_id: str
    component: BehaviorArtifactKind
    environment: str
    scope_digest: Sha256
    target_thread_id: str | None = None
    target_workstream: str | None = None
    revision: int = Field(ge=1)
    change_kind: Literal["PROMOTED", "ROLLED_BACK"] = "PROMOTED"
    artifact_ref: str
    content_digest: Sha256
    previous_digest: Sha256
    exposure_ref: str
    approval_ref: str
    actor_ref: str
    created_at: AwareDatetime
    receipt_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> Self:
        if self.target_thread_id is not None and self.target_workstream is not None:
            raise ValueError("BEHAVIOR_BASELINE_TARGET_AMBIGUOUS")
        expected = domain_digest(
            "BEHAVIOR_BASELINE_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": self.project_id,
                    "component": self.component,
                    "environment": self.environment,
                    "target_thread_id": self.target_thread_id,
                    "target_workstream": self.target_workstream,
                }
            ),
        )
        if self.scope_digest != expected:
            raise ValueError("BEHAVIOR_BASELINE_SCOPE_MISMATCH")
        check_digest(self, "receipt_digest", "BEHAVIOR_BASELINE_REVISION")
        return self


class BehaviorSelection(DomainModel):
    snapshots: tuple[ActiveBehaviorSnapshot, ...]
    shadows: tuple[ActiveBehaviorSnapshot, ...] = ()


def select_behavior_baseline(
    values: tuple[BehaviorBaselineRevision, ...], thread_id: str | None, workstream: str | None
) -> BehaviorBaselineRevision | None:
    matches = tuple(
        item
        for item in values
        if (item.target_thread_id is None or item.target_thread_id == thread_id)
        and (item.target_workstream is None or item.target_workstream == workstream)
    )
    return max(
        matches,
        key=lambda item: 2 if item.target_thread_id else 1 if item.target_workstream else 0,
        default=None,
    )
