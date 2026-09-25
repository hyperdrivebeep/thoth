from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256


class ImprovementEvaluationUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvaluatorIsolation(StrEnum):
    IN_PROCESS_DETERMINISTIC = "IN_PROCESS_DETERMINISTIC"
    SUBPROCESS = "SUBPROCESS"
    SANDBOX = "SANDBOX"


class EvaluatorCapability(DomainModel):
    evaluator_id: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    input_schema_digest: Sha256
    output_schema_digest: Sha256
    isolation: EvaluatorIsolation
    max_requests: int = Field(ge=1, le=10_000)
    max_timeout_seconds: int = Field(ge=1, le=3_600)
    result_authority: str = "CANDIDATE_ONLY"
