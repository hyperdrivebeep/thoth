"""Observation status describes the existing improvement lifecycle, not a new one."""

from typing import Literal

from thoth.domain.base import DomainModel


class ImprovementObservation(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    trigger_state: Literal["NOT_TRIGGERED", "OBSERVED", "EVALUATED", "HELD"]
    reason_code: str
    failure_observation_ref: str | None = None
    evaluation_run_ref: str | None = None
    active_baseline_digest: str | None = None
    result_ref: str | None = None
