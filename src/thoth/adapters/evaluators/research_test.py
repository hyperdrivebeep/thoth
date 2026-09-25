"""Versioned measurement reducers; runtime output never supplies a validity or fit score."""

from __future__ import annotations

from decimal import Decimal

from thoth.domain.test_validity import ResearchMeasurementContract, ResearchMeasurementObservation
from thoth.ports.test_validity import MeasurementReducerPort, ResearchTestEvaluatorPort


class ArithmeticMeanMeasurementReducer(MeasurementReducerPort):
    def reduce(self, observation: ResearchMeasurementObservation) -> Decimal:
        return sum(observation.samples, Decimal(0)) / Decimal(len(observation.samples))


class RegisteredResearchTestEvaluator(ResearchTestEvaluatorPort):
    def __init__(self) -> None:
        self._reducers: dict[tuple[str, str], MeasurementReducerPort] = {}

    def register(self, method: str, version: str, reducer: MeasurementReducerPort) -> None:
        key = (method, version)
        if key in self._reducers:
            raise ValueError("MEASUREMENT_PRODUCER_ALREADY_REGISTERED")
        self._reducers[key] = reducer

    def evaluate(
        self, contract: ResearchMeasurementContract, observation: ResearchMeasurementObservation
    ) -> Decimal:
        reducer = self._reducers.get((contract.method, contract.procedure_version))
        if reducer is None:
            raise ValueError("MEASUREMENT_PRODUCER_NOT_REGISTERED")
        return reducer.reduce(observation)


def default_research_test_evaluator() -> RegisteredResearchTestEvaluator:
    registry = RegisteredResearchTestEvaluator()
    registry.register("ARITHMETIC_MEAN", "numeric-mean:1", ArithmeticMeanMeasurementReducer())
    return registry
