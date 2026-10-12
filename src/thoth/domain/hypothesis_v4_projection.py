"""Versioned v4 canonical details, including the request that generated each item."""

from typing import Literal

from thoth.domain.base import DomainModel
from thoth.domain.hypothesis_v4 import ExpectedResultV4, TableFillRecordV4
from thoth.domain.research_projection import HypothesisGenerationDetails, PortfolioGenerationDetails
from thoth.domain.research_reference import RevisionRef
from thoth.domain.response_language import ResponseLanguage


class ExpectedByTestV4(DomainModel):
    test_id: str
    rows: tuple[ExpectedResultV4, ...]


class HypothesisGenerationDetailsV4(HypothesisGenerationDetails):
    contract_version: Literal["hypothesis_portfolio.v4"] = "hypothesis_portfolio.v4"  # pyright: ignore[reportIncompatibleVariableOverride]
    expected_results: tuple[ExpectedByTestV4, ...] = ()  # pyright: ignore[reportIncompatibleVariableOverride]
    generating_request_ref: RevisionRef
    response_language: ResponseLanguage


class PortfolioGenerationDetailsV4(PortfolioGenerationDetails):
    contract_version: Literal["hypothesis_portfolio.v4"] = "hypothesis_portfolio.v4"  # pyright: ignore[reportIncompatibleVariableOverride]
    table_fill: TableFillRecordV4 | None = None  # pyright: ignore[reportIncompatibleVariableOverride]
    generating_request_ref: RevisionRef
    response_language: ResponseLanguage
