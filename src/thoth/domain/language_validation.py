"""A bounded script check, not a claim about English fluency or scientific correctness."""

from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.research_projection import DroppedExpectation


class HypothesisLanguageCheck(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    response_language: Literal["en"] = "en"
    scope: Literal["GENERATED_HYPOTHESIS_PROSE"] = "GENERATED_HYPOTHESIS_PROSE"
    check: Literal["HANGUL_OUTSIDE_PROTECTED_TEXT"] = "HANGUL_OUTSIDE_PROTECTED_TEXT"
    flagged_items: int = Field(ge=0)
    hypotheses_checked: int = Field(ge=0)
    findings: tuple[DroppedExpectation, ...]
