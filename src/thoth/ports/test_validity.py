from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from thoth.domain.test_validity import (
    ResearchMeasurementContract,
    ResearchMeasurementObservation,
    TestValidityAssessment,
)


class MeasurementReducerPort(Protocol):
    def reduce(self, observation: ResearchMeasurementObservation) -> Decimal: ...


class ResearchTestEvaluatorPort(Protocol):
    def evaluate(
        self, contract: ResearchMeasurementContract, observation: ResearchMeasurementObservation
    ) -> Decimal: ...


class TestValidityStorePort(Protocol):
    def add_assessment(self, value: TestValidityAssessment) -> None: ...

    def read_assessment(
        self, project_id: str, assessment_id: str
    ) -> TestValidityAssessment | None: ...

    def find_attempt(
        self, project_id: str, prediction_id: str, attempt_ref: str
    ) -> TestValidityAssessment | None: ...
