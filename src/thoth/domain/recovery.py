from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256


class ExecutionFailureClass(StrEnum):
    TRANSIENT_INFRA = "TRANSIENT_INFRA"
    SEMANTIC = "SEMANTIC"
    CODE = "CODE"
    AMBIGUOUS_EXTERNAL = "AMBIGUOUS_EXTERNAL"
    PERMANENT_INFRA = "PERMANENT_INFRA"


class RecoveryAttempt(DomainModel):
    attempt_id: str
    input_context_digest: Sha256
    result_state: str
    failure_class: ExecutionFailureClass | None = None
    failure_detail: str | None = None
    retry_ordinal: int = Field(ge=0)


class RecoveryCandidateRevision(DomainModel):
    candidate_id: str
    action_id: str
    plan_id: str
    baseline_head_digest: Sha256
    failure_class: ExecutionFailureClass
    revised_specification: str
    repair_basis: str
    attempt_ids: tuple[str, ...]
    candidate_digest: Sha256


class ReconciliationRecord(DomainModel):
    reconciliation_id: str
    action_id: str
    attempt_id: str
    state: Literal["REQUIRED"] = "REQUIRED"
    automatic_retry_allowed: Literal[False] = False
    reason: str
    reconciliation_digest: Sha256
