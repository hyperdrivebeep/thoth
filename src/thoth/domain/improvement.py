from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import ProjectId, Sha256


class ImprovementRunState(StrEnum):
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    PROMOTED_LOCAL = "PROMOTED_LOCAL"
    ROLLED_BACK = "ROLLED_BACK"
    REJECTED_SAME_DIGEST = "REJECTED_SAME_DIGEST"
    HELD = "HELD"
    EVALUATED = "EVALUATED"


class ImprovementContextBasis(DomainModel):
    project_id: ProjectId
    project_digest: Sha256
    head_set_digest: Sha256
    policy_id: str
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    baseline_set_digest: Sha256 | None
    registry_digest: Sha256 | None
    actor_context_digest: Sha256 | None
    role_assignment_digest: Sha256 | None
    session_digest: Sha256 | None = None


class ImprovementEvaluationRequest(DomainModel):
    request_id: str
    project_id: ProjectId
    thread_id: str
    baseline_digest: Sha256
    candidate_digest: Sha256
    fixture_digest: Sha256
    hidden_holdout_digest: Sha256
    context: ImprovementContextBasis
    max_requests: int = Field(ge=1, le=8)
    timeout_seconds: int = Field(ge=1, le=60)
    created_at: AwareDatetime
    request_digest: Sha256

    @model_validator(mode="after")
    def verify_request(self) -> ImprovementEvaluationRequest:
        if self.context.project_id != self.project_id:
            raise ValueError("improvement request context project mismatch")
        digest = domain_digest(
            "IMPROVEMENT_EVALUATION_REQUEST",
            "1.0.0",
            canonical_payload(self.model_dump(mode="python", exclude={"request_digest"})),
        )
        if digest != self.request_digest:
            legacy_digest = domain_digest(
                "IMPROVEMENT_EVALUATION_REQUEST",
                "1.0.0",
                canonical_payload(
                    self.model_dump(
                        mode="python",
                        exclude={"request_digest": True, "context": {"session_digest": True}},
                    )
                ),
            )
            if self.context.session_digest is not None or legacy_digest != self.request_digest:
                raise ValueError("improvement request digest mismatch")
        return self


class ImprovementEvaluation(DomainModel):
    evaluator_id: str
    fixture_digest: Sha256
    baseline_digest: Sha256
    candidate_digest: Sha256
    hidden_holdout_digest_ref: Sha256
    baseline_quality_bps: int = Field(ge=0, le=10_000)
    candidate_quality_bps: int = Field(ge=0, le=10_000)
    baseline_safety_bps: int = Field(ge=0, le=10_000)
    candidate_safety_bps: int = Field(ge=0, le=10_000)
    baseline_cost_microunits: int = Field(ge=0)
    candidate_cost_microunits: int = Field(ge=0)
    critical_regression: bool = False
    hidden_holdout_exposed: bool = False
    budget_exhausted: bool = False
    timed_out: bool = False
    evaluation_digest: Sha256


class ImprovementExposure(DomainModel):
    exposure_id: str
    stage: Literal["OFFLINE", "SANDBOX", "SHADOW", "CANARY"]
    project_id: ProjectId
    baseline_digest: Sha256
    candidate_digest: Sha256
    fixture_digest: Sha256
    evaluator_id: str
    exact_candidate_digest: bool
    hidden_holdout_accessed: Literal[False] = False
    external_effect: Literal[False] = False
    exposure_digest: Sha256
    recorded_at: AwareDatetime


class RecursiveImprovementResult(DomainModel):
    run_id: str
    project_id: ProjectId
    thread_id: str
    state: ImprovementRunState
    failure_fingerprint: Sha256
    failure_count: int = Field(ge=1)
    baseline_digest: Sha256
    candidate_digest: Sha256 | None = None
    fixture_digest: Sha256 | None = None
    evaluator_id: str | None = None
    evaluation: ImprovementEvaluation | None = None
    exposures: tuple[ImprovementExposure, ...] = ()
    rollback_reason: str | None = None
    active_digest: Sha256
    hidden_holdout_exposed: bool = False
    exact_digest_canary: bool = False
    official_kpi_changed: Literal[False] = False
    safety_threshold_changed: Literal[False] = False
    waiver_changed: Literal[False] = False
    r4_decision_changed: Literal[False] = False
    model_weights_changed: Literal[False] = False
    semantic_truth_certified: Literal[False] = False
    receipt_digest: Sha256
    created_at: AwareDatetime
    behavior_artifact_id: str | None = None
    behavior_artifact_kind: str | None = None
    behavior_registry_active_digest: Sha256 | None = None
    evaluation_provenance: Literal["TEST_ONLY", "MEASURED_PAIR"] | None = None
    paired_evaluation_id: str | None = None
    paired_result_digest: Sha256 | None = None
