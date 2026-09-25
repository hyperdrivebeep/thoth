from __future__ import annotations

from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.enums import EvidenceEffect
from thoth.domain.ids import Sha256


class R2OutcomeValidity(DomainModel):
    process_state: str = Field(min_length=1, max_length=80)
    observation_state: str = Field(min_length=1, max_length=80)
    comparator_state: str = Field(min_length=1, max_length=80)
    scientific_truth_state: Literal["NOT_CERTIFIED"] = "NOT_CERTIFIED"
    criterion_state: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"
    reasons: tuple[str, ...] = Field(min_length=1)
    validity_digest: Sha256


class HypothesisExecutionAppraisal(DomainModel):
    action_id: str
    attempt_id: str
    observation_refs: tuple[str, ...]
    effect: EvidenceEffect = EvidenceEffect.NEUTRAL
    reason: str
    appraisal_digest: Sha256


class R2ExecutionSummary(DomainModel):
    action_id: str
    attempt_id: str
    sandbox_receipt_digest: Sha256
    observation_refs: tuple[str, ...]
    outcome_id: str
    validity: R2OutcomeValidity
    current_head_set_digest: Sha256
    input_context_digest: Sha256
    semantic_truth_certified: Literal[False] = False
