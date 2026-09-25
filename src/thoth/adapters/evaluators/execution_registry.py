"""Explicit execution capabilities for measured comparisons."""

from thoth.adapters.evaluators.isolated_runner import (
    BehaviorProgramExecutor,
    IsolatedProgramExecutor,
)
from thoth.domain.evaluation_run import EvaluationRunError
from thoth.ports.evaluation_runner import EvaluationExecutionPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class EvaluationExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, EvaluationExecutionPort] = {}

    def register(self, executor: EvaluationExecutionPort) -> None:
        if executor.executor_id in self._executors:
            raise EvaluationRunError("EVALUATION_EXECUTOR_DUPLICATED")
        self._executors[executor.executor_id] = executor

    def resolve(self, executor_id: str) -> EvaluationExecutionPort:
        value = self._executors.get(executor_id)
        if value is None:
            raise EvaluationRunError("EVALUATION_EXECUTOR_NOT_CONFIGURED")
        return value


def default_execution_registry(
    objects: ObjectStorePort, clock: ClockPort, ids: IdGeneratorPort
) -> EvaluationExecutorRegistry:
    registry = EvaluationExecutorRegistry()
    registry.register(IsolatedProgramExecutor(objects=objects, clock=clock, ids=ids))
    registry.register(BehaviorProgramExecutor(objects=objects, clock=clock, ids=ids))
    return registry
