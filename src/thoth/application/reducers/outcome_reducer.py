from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from thoth.domain.enums import EvidenceEffect
from thoth.domain.errors import InvariantViolation
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.outcome import HypothesisEvidenceUpdate, HypothesisScore, PortfolioRanking


def rank_hypotheses(
    *,
    portfolio: HypothesisPortfolio,
    evidence: tuple[EvidenceSpan, ...],
    updates: tuple[HypothesisEvidenceUpdate, ...],
    previous: PortfolioRanking | None,
    input_head_set_digest: str,
) -> PortfolioRanking:
    hypothesis_ids = {item.hypothesis_id for item in portfolio.hypotheses}
    evidence_ids = {item.span_id for item in evidence}
    scores: dict[str, Decimal] = defaultdict(Decimal)
    supports: dict[str, list[str]] = defaultdict(list)
    counters: dict[str, list[str]] = defaultdict(list)
    for update in updates:
        if update.hypothesis_id not in hypothesis_ids:
            raise InvariantViolation("outcome update references an unknown hypothesis")
        if update.evidence_span_id not in evidence_ids:
            raise InvariantViolation("outcome update references unknown evidence")
        if update.effect == EvidenceEffect.SUPPORT:
            scores[update.hypothesis_id] += update.weight
            supports[update.hypothesis_id].append(update.evidence_span_id)
        elif update.effect == EvidenceEffect.COUNTER:
            scores[update.hypothesis_id] -= update.weight
            counters[update.hypothesis_id].append(update.evidence_span_id)

    ordered = tuple(
        HypothesisScore(
            hypothesis_id=hypothesis.hypothesis_id,
            score=scores[hypothesis.hypothesis_id],
            supporting_refs=tuple(sorted(supports[hypothesis.hypothesis_id])),
            counter_refs=tuple(sorted(counters[hypothesis.hypothesis_id])),
        )
        for hypothesis in sorted(
            portfolio.hypotheses,
            key=lambda item: (-scores[item.hypothesis_id], item.hypothesis_id),
        )
    )
    previous_order = (
        () if previous is None else tuple(item.hypothesis_id for item in previous.ordered_scores)
    )
    current_order = tuple(item.hypothesis_id for item in ordered)
    material_change = (
        previous is None or previous_order != current_order or previous.ordered_scores != ordered
    )
    return PortfolioRanking(
        portfolio_id=portfolio.portfolio_id,
        ordered_scores=ordered,
        previous_order=previous_order,
        material_change=material_change,
        reason=(
            "ranking changed after evidence update"
            if material_change
            else "new evidence produced no material ranking change"
        ),
        input_head_set_digest=input_head_set_digest,
    )
