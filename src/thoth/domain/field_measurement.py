from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class FieldProtocolSeal(DomainModel):
    protocol_id: str
    project_id: ProjectId
    protocol_version: str
    case_digests: dict[str, Sha256]
    arms: tuple[Literal["A", "B", "C"], ...]
    sequence_matrix: tuple[str, ...]
    baseline_toolchain: tuple[str, ...]
    thresholds: dict[str, int]
    hard_zero_metrics: tuple[str, ...]
    state: Literal["SEALED_BEFORE_RESULTS"] = "SEALED_BEFORE_RESULTS"
    external_results: Literal["NOT_RUN"] = "NOT_RUN"
    protocol_digest: Sha256
    sealed_at: AwareDatetime

    @model_validator(mode="after")
    def require_balanced_abc(self) -> FieldProtocolSeal:
        if set(self.arms) != {"A", "B", "C"}:
            raise ValueError("field protocol requires exactly A/B/C arms")
        if any(set(sequence) != {"A", "B", "C"} for sequence in self.sequence_matrix):
            raise ValueError("each sequence must contain A/B/C exactly once")
        return self


class FieldSessionRecord(DomainModel):
    session_id: str
    project_id: ProjectId
    protocol_digest: Sha256
    reviewer_pseudonym: str
    case_id: str
    arm: Literal["A", "B", "C"]
    sequence_position: int = Field(ge=1)
    state: Literal["RUNNING", "ENDED"] = "RUNNING"
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    timeout: bool = False
    session_digest: Sha256


class FieldEventRecord(DomainModel):
    event_id: str
    project_id: ProjectId
    session_id: str
    event_type: str
    metric_delta: int = Field(default=0, ge=0)
    method_code: str | None = None
    outcome_code: str | None = None
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)
    event_digest: Sha256
    recorded_at: AwareDatetime


class FieldSessionMetrics(DomainModel):
    session_id: str
    active_milliseconds: int = Field(ge=0)
    rpc_operation_count: int = Field(ge=0)
    app_switch_count: int = Field(ge=0)
    manual_reentry_count: int = Field(ge=0)
    correction_count: int = Field(ge=0)
    timeout: bool
    metrics_digest: Sha256


class FieldScoreRecord(DomainModel):
    score_id: str
    project_id: ProjectId
    session_id: str
    scorer_pseudonym: str
    gold_issue_total: int = Field(ge=0)
    critical_issue_detected: int = Field(ge=0)
    decision_completeness_bps: int = Field(ge=0, le=10_000)
    source_span_valid_count: int = Field(ge=0)
    source_span_invalid_count: int = Field(ge=0)
    hard_zero_values: dict[str, int]
    score_digest: Sha256
    recorded_at: AwareDatetime


class FieldExportBundle(DomainModel):
    export_id: str
    project_id: ProjectId
    protocol: FieldProtocolSeal
    sessions: tuple[FieldSessionRecord, ...]
    events: tuple[FieldEventRecord, ...]
    metrics: tuple[FieldSessionMetrics, ...]
    scores: tuple[FieldScoreRecord, ...]
    purpose: str
    privacy_safe: Literal[True] = True
    external_results: Literal["NOT_RUN"] = "NOT_RUN"
    field_validated: Literal[False] = False
    d6_claimed: Literal[False] = False
    external_participant_sessions: Literal["NOT_RUN"] = "NOT_RUN"
    external_expert_validation: Literal["NOT_RUN"] = "NOT_RUN"
    time_saving_result: Literal["NOT_RUN"] = "NOT_RUN"
    wtp_result: Literal["NOT_RUN"] = "NOT_RUN"
    bundle_digest: Sha256
    created_at: AwareDatetime


class SealedFieldBaseline(DomainModel):
    baseline_id: str
    version: str
    case_digests: dict[str, Sha256]
    case_paths: dict[str, str] = Field(default_factory=dict)
    sequence_matrix: tuple[str, ...]
    baseline_toolchain: tuple[str, ...]
    hard_zero_metrics: tuple[str, ...]
    timebox_seconds: int = Field(ge=60, le=14_400)
    sealed_at: AwareDatetime
    external_results: Literal["NOT_RUN"] = "NOT_RUN"
    baseline_digest: Sha256

    @model_validator(mode="after")
    def require_main_six_by_six(self) -> SealedFieldBaseline:
        if len(self.case_digests) != 6:
            raise ValueError("sealed field baseline requires six cases")
        if self.case_paths and set(self.case_paths) != set(self.case_digests):
            raise ValueError("sealed field baseline case paths must match case digests")
        required_sequences = {"ABC", "BCA", "CAB", "ACB", "CBA", "BAC"}
        if set(self.sequence_matrix) != required_sequences:
            raise ValueError("sealed field baseline requires all six counterbalanced sequences")
        return self


class FieldAssignment(DomainModel):
    assignment_id: str
    reviewer_pseudonym: str
    case_id: str
    arm: Literal["A", "B", "C"]
    sequence_code: str
    sequence_position: int = Field(ge=1, le=6)
    assignment_digest: Sha256


class FieldAssignmentManifest(DomainModel):
    baseline_digest: Sha256
    assignments: tuple[FieldAssignment, ...]
    reviewer_arm_counts: dict[str, dict[str, int]]
    case_arm_counts: dict[str, dict[str, int]]
    collision_free: Literal[True] = True
    external_sessions: Literal["NOT_RUN"] = "NOT_RUN"
    manifest_digest: Sha256


class BlindScorePackage(DomainModel):
    submission_id: str
    session_id: str
    normalized_assessment: dict[str, object]
    source_digest: Sha256
    arm_blinded: Literal[True] = True
    identity_fields_removed: Literal[True] = True
    package_digest: Sha256


class FieldAdjudicationRecord(DomainModel):
    adjudication_id: str
    score_digests: tuple[Sha256, Sha256]
    state: Literal["AGREED", "ADJUDICATION_REQUIRED", "RESOLVED"]
    resolution: dict[str, int] | None = None
    adjudicator_pseudonym: str | None = None
    scorer_identity_exposed: Literal[False] = False
    record_digest: Sha256


class PrivacySafeFieldExport(DomainModel):
    protocol_digest: Sha256
    data: dict[str, object]
    privacy_safe: Literal[True] = True
    external_participant_sessions: Literal["NOT_RUN"] = "NOT_RUN"
    external_expert_validation: Literal["NOT_RUN"] = "NOT_RUN"
    time_saving_result: Literal["NOT_RUN"] = "NOT_RUN"
    wtp_result: Literal["NOT_RUN"] = "NOT_RUN"
    d6_claimed: Literal[False] = False
    export_digest: Sha256
