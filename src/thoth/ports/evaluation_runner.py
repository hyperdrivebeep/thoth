from __future__ import annotations

from typing import Literal, Protocol

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.evaluation_run import (
    CompiledEvaluationProgram,
    EvaluationBinding,
    EvaluationExecutionReceipt,
    EvaluationInput,
    EvaluationRunSpec,
    PairedEvaluationResult,
    PairedRunRecord,
)


class EvaluationCatalogPort(Protocol):
    def resolve(self, project_id: str, binding_id: str) -> EvaluationBinding: ...
    def default_binding(
        self, project_id: str, component: BehaviorArtifactKind
    ) -> EvaluationBinding | None: ...


class EvaluationExecutionPort(Protocol):
    executor_id: str
    version: str

    async def execute(
        self,
        spec: EvaluationRunSpec,
        arm: Literal["BASELINE", "CANDIDATE"],
        program: CompiledEvaluationProgram,
        inputs: tuple[EvaluationInput, ...],
        initial_memory: dict[str, object],
    ) -> EvaluationExecutionReceipt: ...


class EvaluationRunStorePort(Protocol):
    def claim(
        self, value: PairedRunRecord, *, reuse_limit: int
    ) -> tuple[PairedRunRecord, bool]: ...
    def read(self, project_id: str, pair_id: str) -> PairedRunRecord | None: ...
    def read_plan(
        self, project_id: str, plan_id: str, plan_digest: str
    ) -> PairedRunRecord | None: ...
    def update(self, value: PairedRunRecord, *, expected_revision: int) -> None: ...


class EvaluationExecutorRegistryPort(Protocol):
    def resolve(self, executor_id: str) -> EvaluationExecutionPort: ...


class EvaluationScoringPort(Protocol):
    def score(
        self,
        spec: EvaluationRunSpec,
        binding: EvaluationBinding,
        baseline: EvaluationExecutionReceipt,
        candidate: EvaluationExecutionReceipt,
    ) -> PairedEvaluationResult: ...
