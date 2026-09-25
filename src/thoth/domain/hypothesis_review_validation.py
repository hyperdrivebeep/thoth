"""Coverage contract for hypothesis review. Portfolio size is not a global constant."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from thoth.domain.base import DomainModel


class HypothesisReviewCoverage(DomainModel):
    target_ids: tuple[str, ...]
    received_ids: tuple[str, ...]
    missing: tuple[str, ...]
    duplicate: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.duplicate and not self.unexpected


class HypothesisReviewItem(Protocol):
    @property
    def hypothesis_id(self) -> str: ...


class HypothesisReviewPortfolio(Protocol):
    @property
    def hypotheses(self) -> Sequence[HypothesisReviewItem]: ...


def target_ids(portfolio: HypothesisReviewPortfolio) -> tuple[str, ...]:
    hypotheses: Sequence[HypothesisReviewItem] = getattr(portfolio, "hypotheses", ())
    return tuple(str(item.hypothesis_id) for item in hypotheses)


def review_coverage(
    portfolio: HypothesisReviewPortfolio,
    decisions: Sequence[HypothesisReviewItem],
) -> HypothesisReviewCoverage:
    targets = target_ids(portfolio)
    received = tuple(item.hypothesis_id for item in decisions)
    target_set = set(targets)
    seen: set[str] = set()
    duplicate: list[str] = []
    for item in received:
        if item in seen:
            duplicate.append(item)
        seen.add(item)
    return HypothesisReviewCoverage(
        target_ids=targets,
        received_ids=received,
        missing=tuple(item for item in targets if item not in seen),
        duplicate=tuple(duplicate),
        unexpected=tuple(item for item in received if item not in target_set),
    )
