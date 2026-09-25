from __future__ import annotations

from thoth.domain.enums import CausalLocus
from thoth.domain.errors import InvariantViolation
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import HypothesisPortfolio


def validate_hypothesis_portfolio(
    portfolio: HypothesisPortfolio,
    *,
    evidence: tuple[EvidenceSpan, ...],
    require_unknown_alternative: bool,
) -> HypothesisPortfolio:
    evidence_ids = {span.span_id for span in evidence}
    for hypothesis in portfolio.hypotheses:
        if hypothesis.object_id != portfolio.object_id:
            raise InvariantViolation("hypothesis belongs to a different decision object")
        unknown_support = set(hypothesis.support_evidence_refs) - evidence_ids
        unknown_counter = set(hypothesis.counterevidence_refs) - evidence_ids
        if unknown_support or unknown_counter:
            raise InvariantViolation("hypothesis references evidence outside the context pack")
        if not hypothesis.counterevidence_refs and not hypothesis.counterevidence_queries:
            raise InvariantViolation("hypothesis requires an independent counterevidence query")
    if require_unknown_alternative and portfolio.uncertainty_reserve == "UNASSESSED" and not any(
        hypothesis.primary_locus == CausalLocus.OTHER_WITH_DESCRIPTION
        for hypothesis in portfolio.hypotheses
    ):
        raise InvariantViolation("insufficient evidence requires an OTHER/UNKNOWN alternative")
    return portfolio
