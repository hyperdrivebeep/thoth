"""Frozen executable comparisons, measured receipts, and independent score vectors."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import BehaviorPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import Sha256
from thoth.domain.improvement import ImprovementContextBasis


class EvaluationRunError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Expression(DomainModel):
    op: Literal["literal", "input", "memory", "add", "subtract", "equal", "if"]
    value: JsonValue = None
    key: str | None = Field(default=None, max_length=160)
    args: tuple[Expression, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def shape(self) -> Expression:
        arity = {
            "literal": 0,
            "input": 0,
            "memory": 0,
            "add": 2,
            "subtract": 2,
            "equal": 2,
            "if": 3,
        }[self.op]
        if len(self.args) != arity or ((self.op in {"input", "memory"}) != (self.key is not None)):
            raise ValueError("EVALUATION_PROGRAM_SHAPE_INVALID")
        return self


class EvaluationProgram(DomainModel):
    schema_version: Literal["PURE_TRANSFORM_V1"] = "PURE_TRANSFORM_V1"
    outputs: dict[str, Expression] = Field(min_length=1, max_length=32)
    memory_updates: dict[str, Expression] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def bounded(self) -> EvaluationProgram:
        frontier = [(node, 0) for node in (*self.outputs.values(), *self.memory_updates.values())]
        count = 0
        while frontier:
            node, depth = frontier.pop()
            count += 1
            if count > 256 or depth > 16:
                raise ValueError("EVALUATION_PROGRAM_LIMIT")
            frontier.extend((child, depth + 1) for child in node.args)
        canonical_payload(self)
        return self


class EvaluationInput(DomainModel):
    case_id: str = Field(min_length=1, max_length=160)
    payload: dict[str, JsonValue]


class BehaviorEvaluationProgram(DomainModel):
    schema_version: Literal["BEHAVIOR_COMPONENT_V1"] = "BEHAVIOR_COMPONENT_V1"
    policy: BehaviorPolicy


CompiledEvaluationProgram = EvaluationProgram | BehaviorEvaluationProgram


class ScoringCase(DomainModel):
    public: EvaluationInput
    expected_output: dict[str, JsonValue]
    axis: Literal[
        "quality", "evidence", "authority", "cutoff", "privacy", "abstention", "regression"
    ] = "quality"


class EvaluationCasePack(DomainModel):
    project_id: str
    pack_id: str
    version: str
    cases: tuple[ScoringCase, ...] = Field(min_length=1, max_length=64)
    initial_memory: dict[str, JsonValue] = Field(default_factory=dict)
    resource_refs: tuple[str, ...] = ()
    expected_visibility: Literal["SCORER_ONLY"] = "SCORER_ONLY"
    expected_values_exposed: bool = False
    max_pair_runs: int = Field(default=1, ge=1, le=64)
    pack_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> EvaluationCasePack:
        if len({case.public.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("EVALUATION_CASE_IDS_DUPLICATED")
        check_digest(self, "pack_digest", "EVALUATION_CASE_PACK")
        return self


class EvaluationScorer(DomainModel):
    scorer_id: str
    version: str
    algorithm: Literal["EXACT_JSON_V1"] = "EXACT_JSON_V1"
    scorer_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> EvaluationScorer:
        check_digest(self, "scorer_digest", "EVALUATION_SCORER")
        return self


class EvaluationBinding(DomainModel):
    project_id: str
    binding_id: str
    component: BehaviorArtifactKind
    executor_id: str = "PURE_TRANSFORM_SUBPROCESS_V1"
    case_pack: EvaluationCasePack
    scorer: EvaluationScorer
    max_requests: int = Field(default=128, ge=2, le=128)
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    max_output_bytes: int = Field(default=262144, ge=128, le=1048576)
    binding_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> EvaluationBinding:
        if self.case_pack.project_id != self.project_id:
            raise ValueError("EVALUATION_BINDING_PROJECT_MISMATCH")
        check_digest(self, "binding_digest", "EVALUATION_BINDING")
        return self


class ExecutableArtifact(DomainModel):
    project_id: str
    artifact_ref: str
    component: BehaviorArtifactKind
    content: dict[str, JsonValue]
    content_digest: Sha256
    digest_domain: Literal["BEHAVIOR_ARTIFACT_CONTENT", "IMPROVEMENT_CANDIDATE"]

    @model_validator(mode="after")
    def sealed(self) -> ExecutableArtifact:
        if (
            domain_digest(self.digest_domain, "1.0.0", canonical_payload(self.content))
            != self.content_digest
        ):
            raise ValueError("EVALUATION_ARTIFACT_DIGEST_MISMATCH")
        return self


class EvaluationRunSpec(DomainModel):
    pair_id: str
    project_id: str
    plan_id: str
    plan_digest: Sha256
    component: BehaviorArtifactKind
    baseline_ref: str
    baseline_digest: Sha256
    candidate_ref: str
    candidate_digest: Sha256
    binding_id: str
    binding_digest: Sha256
    fixture_digest: Sha256
    scorer_digest: Sha256
    public_input_digest: Sha256
    initial_memory_digest: Sha256
    context: ImprovementContextBasis
    resource_refs: tuple[str, ...]
    component_registry_digest: Sha256 | None = None
    max_requests: int
    max_output_bytes: int
    timeout_seconds: int
    created_at: AwareDatetime
    deadline_at: AwareDatetime
    request_digest: Sha256
    dedupe_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> EvaluationRunSpec:
        if self.context.project_id != self.project_id or self.deadline_at <= self.created_at:
            raise ValueError("EVALUATION_REQUEST_CONTEXT_INVALID")
        check_digest(self, "request_digest", "EVALUATION_RUN_SPEC")
        return self


class EvaluationExecutionReceipt(DomainModel):
    execution_id: str
    pair_id: str
    project_id: str
    arm: Literal["BASELINE", "CANDIDATE"]
    artifact_digest: Sha256
    input_digest: Sha256
    initial_memory_digest: Sha256
    final_memory_digest: Sha256 | None
    output_blob_digest: Sha256
    workspace_id: str
    executor_id: str
    executor_version: str
    isolation: Literal["TRUSTED_DECLARATIVE_SUBPROCESS"] = "TRUSTED_DECLARATIVE_SUBPROCESS"
    state: Literal["SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED", "OUTPUT_LIMIT"]
    case_count: int = Field(ge=0)
    elapsed_ns: int = Field(ge=0)
    billed_cost_microunits: Literal[0] = 0
    cost_basis: Literal["NO_BILLABLE_PROVIDER_CALLS"] = "NO_BILLABLE_PROVIDER_CALLS"
    started_at: AwareDatetime
    completed_at: AwareDatetime
    receipt_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> EvaluationExecutionReceipt:
        check_digest(self, "receipt_digest", "EVALUATION_EXECUTION_RECEIPT")
        return self


class EvaluationMetricVector(DomainModel):
    correct_cases: int | None = Field(ge=0)
    total_cases: int = Field(ge=1)
    quality_bps: int | None = Field(ge=0, le=10000)
    axis_correct: dict[str, int]
    axis_totals: dict[str, int]
    hard_failures: tuple[str, ...]
    abstention_correct: int = Field(ge=0)
    elapsed_ns: int = Field(ge=0)
    billed_cost_microunits: Literal[0] = 0


class PairedEvaluationResult(DomainModel):
    pair_id: str
    project_id: str
    request_digest: Sha256
    fixture_digest: Sha256
    scorer_id: str
    scorer_digest: Sha256
    baseline: EvaluationExecutionReceipt
    candidate: EvaluationExecutionReceipt
    baseline_metrics: EvaluationMetricVector
    candidate_metrics: EvaluationMetricVector
    validity: Literal["VALID", "INVALID", "LIMITED"]
    performance_verdict: Literal["BETTER", "NO_MATERIAL_CHANGE", "WORSE", "MIXED", "INCONCLUSIVE"]
    exposure_stage: Literal["OFFLINE"] = "OFFLINE"
    exposure_id: str
    promotion_eligible: Literal[False] = False
    semantic_truth_certified: Literal[False] = False
    result_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> PairedEvaluationResult:
        for receipt, arm in ((self.baseline, "BASELINE"), (self.candidate, "CANDIDATE")):
            if (receipt.project_id, receipt.pair_id, receipt.arm) != (
                self.project_id,
                self.pair_id,
                arm,
            ):
                raise ValueError("EVALUATION_PAIR_EXECUTION_MISMATCH")
        if self.baseline.workspace_id == self.candidate.workspace_id:
            raise ValueError("EVALUATION_WORKSPACE_NOT_ISOLATED")
        if (self.baseline.input_digest, self.baseline.initial_memory_digest) != (
            self.candidate.input_digest,
            self.candidate.initial_memory_digest,
        ):
            raise ValueError("EVALUATION_PAIR_INPUT_MISMATCH")
        check_digest(self, "result_digest", "PAIRED_EVALUATION_RESULT")
        return self


class PairedRunRecord(DomainModel):
    spec: EvaluationRunSpec
    state: Literal["RUNNING", "PARTIAL", "COMPLETE", "HELD", "CANCELLED"]
    revision: int = Field(ge=0)
    baseline: EvaluationExecutionReceipt | None = None
    candidate: EvaluationExecutionReceipt | None = None
    result: PairedEvaluationResult | None = None
    reason_code: str | None = None
    active_arm: Literal["BASELINE", "CANDIDATE"] | None = None
    record_digest: Sha256

    @model_validator(mode="after")
    def sealed(self) -> PairedRunRecord:
        for receipt, arm, digest in (
            (self.baseline, "BASELINE", self.spec.baseline_digest),
            (self.candidate, "CANDIDATE", self.spec.candidate_digest),
        ):
            if receipt is not None and (
                receipt.project_id != self.spec.project_id
                or receipt.pair_id != self.spec.pair_id
                or receipt.arm != arm
                or receipt.artifact_digest != digest
                or receipt.input_digest != self.spec.public_input_digest
                or receipt.initial_memory_digest != self.spec.initial_memory_digest
            ):
                raise ValueError("EVALUATION_RECORD_RECEIPT_MISMATCH")
        if self.state == "COMPLETE" and self.result is None:
            raise ValueError("EVALUATION_COMPLETE_RESULT_MISSING")
        if self.result is not None and (
            self.result.project_id != self.spec.project_id
            or self.result.pair_id != self.spec.pair_id
            or self.result.request_digest != self.spec.request_digest
        ):
            raise ValueError("EVALUATION_RECORD_RESULT_MISMATCH")
        check_digest(self, "record_digest", "EVALUATION_RUN_RECORD")
        return self


def check_digest(model: DomainModel, field: str, domain: str) -> None:
    payload = model.model_dump(mode="python", exclude={field})
    if getattr(model, field) != domain_digest(domain, "1.0.0", canonical_payload(payload)):
        raise ValueError(domain + "_DIGEST_MISMATCH")


def sealed_payload(domain: str, field: str, payload: dict[str, object]) -> dict[str, object]:
    return {**payload, field: domain_digest(domain, "1.0.0", canonical_payload(payload))}
