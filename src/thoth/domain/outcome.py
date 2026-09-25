from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime

from thoth.domain.base import DomainModel
from thoth.domain.enums import EvidenceEffect, OutcomeStatus
from thoth.domain.ids import ActionId, EvidenceSpanId, HypothesisId, ProjectId, Sha256


class HypothesisEvidenceUpdate(DomainModel):
    hypothesis_id: HypothesisId
    evidence_span_id: EvidenceSpanId
    effect: EvidenceEffect
    weight: Decimal
    reason: str


class HypothesisScore(DomainModel):
    hypothesis_id: HypothesisId
    score: Decimal
    supporting_refs: tuple[EvidenceSpanId, ...]
    counter_refs: tuple[EvidenceSpanId, ...]


class PortfolioRanking(DomainModel):
    portfolio_id: str
    ordered_scores: tuple[HypothesisScore, ...]
    previous_order: tuple[HypothesisId, ...] = ()
    material_change: bool
    reason: str
    input_head_set_digest: Sha256


class OutcomeRecord(DomainModel):
    outcome_id: str
    project_id: ProjectId
    action_id: ActionId
    status: OutcomeStatus
    observed_evidence_refs: tuple[EvidenceSpanId, ...]
    hypothesis_updates: tuple[HypothesisEvidenceUpdate, ...]
    interpretation: str
    limitations: tuple[str, ...]
    recorded_at: AwareDatetime
    input_head_set_digest: Sha256
    test_validity_assessment_refs: tuple[str, ...] = ()
    schema_version: str = "1.0.0"
