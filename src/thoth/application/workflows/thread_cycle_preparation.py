"""Pure cycle preparation over values already captured by ThreadCycleService.execute."""

from thoth.domain.enums import HypothesisStatus, PortfolioStatus
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.revision import StagedRevision


def draft_portfolio(
    portfolio: HypothesisPortfolio,
    review_id: str | None,
) -> HypothesisPortfolio:
    return portfolio.model_copy(
        update={
            "status": PortfolioStatus.DRAFT,
            "hypotheses": tuple(
                h.model_copy(
                    update={
                        "status": HypothesisStatus.DRAFT,
                        "semantic_review_ref": review_id,
                    }
                )
                for h in portfolio.hypotheses
            ),
        }
    )


def expected_cycle_heads(
    heads: dict[str, str],
    staged: tuple[StagedRevision, ...],
    expected_head_overrides: dict[str, str] | None,
) -> dict[str, str]:
    staged_keys = {
        f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged
    }
    expected_heads = {key: value for key, value in heads.items() if key in staged_keys}
    expected_heads.update(expected_head_overrides or {})
    return expected_heads
