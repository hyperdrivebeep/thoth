"""Earlier model-role output handed to a later role with its origin and what was checked."""

from typing import Literal

from pydantic import field_validator, model_validator

from thoth.domain.base import DomainModel

MODEL_INTERPRETATION_VIEW_VERSION = "1.0.0"
MODEL_INTERPRETATION_LABEL = "MODEL_INTERPRETATION"
MODEL_INTERPRETATION_CONTRACT = (
    "Items marked MODEL_INTERPRETATION were written by an earlier model role; "
    "only their cited span IDs were checked, not their truth."
)


class ModelInterpretationItem(DomainModel):
    """One earlier-role statement. The text is unchanged; only its origin and check are added."""

    label: Literal["MODEL_INTERPRETATION"] = MODEL_INTERPRETATION_LABEL
    producer_role: Literal["SEMANTIC_REVIEWER", "RESEARCH_PLANNER"]
    item_kind: Literal["REVIEWER_FINDING", "PLANNER_OPEN_ITEM"]
    # CITED_SPAN_IDS_ONLY: the cited IDs were verified to be inputs the producer received.
    # NOT_CHECKED: nothing about the statement was checked.
    check: Literal["CITED_SPAN_IDS_ONLY", "NOT_CHECKED"]
    statement: str
    evidence_refs: tuple[str, ...] = ()
    requirement_id: str | None = None
    finding_kind: Literal["DOCUMENT_FACT", "VALID_NEGATIVE_FINDING"] | None = None

    @model_validator(mode="after")
    def _finding_cites_and_has_a_kind(self) -> "ModelInterpretationItem":
        # An adopted finding always cites a supplied span and names its kind.
        if self.item_kind == "REVIEWER_FINDING" and (
            not self.evidence_refs or self.finding_kind is None
        ):
            raise ValueError("MODEL_INTERPRETATION_FINDING_INCOMPLETE")
        return self


class ModelInterpretationView(DomainModel):
    schema_version: Literal["1.0.0"] = MODEL_INTERPRETATION_VIEW_VERSION
    contract: str = MODEL_INTERPRETATION_CONTRACT
    items: tuple[ModelInterpretationItem, ...]

    @field_validator("contract")
    @classmethod
    def _fixed_contract(cls, value: str) -> str:
        if value != MODEL_INTERPRETATION_CONTRACT:
            raise ValueError("MODEL_INTERPRETATION_CONTRACT_CHANGED")
        return value
