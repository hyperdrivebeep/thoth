"""Transient accepted candidate and row origins, not an independent canonical record."""

from dataclasses import dataclass, field

from thoth.domain.hypothesis_v5 import HypothesisPortfolioV5
from thoth.domain.prediction_grounding import GroundingRowDiagnostic, GroundingStage

PredictionCell = tuple[str, str, str]


@dataclass(frozen=True)
class GroundedPortfolioResult:
    portfolio: HypothesisPortfolioV5
    diagnostics: tuple[GroundingRowDiagnostic, ...] = ()
    row_stages: dict[PredictionCell, GroundingStage] = field(default_factory=dict)
