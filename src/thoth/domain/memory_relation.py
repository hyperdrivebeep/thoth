"""How two stored memories relate, and what a model may propose when rules cannot tell."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel


class MemoryRelation(StrEnum):
    DUPLICATE = "DUPLICATE"
    CONTAINS = "CONTAINS"
    UPDATE = "UPDATE"
    CONTRADICTION_CANDIDATE = "CONTRADICTION_CANDIDATE"
    UNRELATED = "UNRELATED"
    AMBIGUOUS = "AMBIGUOUS"


class MemoryRelationText(DomainModel):
    memory_id: str
    text: str = Field(max_length=1_000)


class MemoryRelationQuestion(DomainModel):
    """Two memories in the order they are shown to the model; the order is recorded."""

    first: MemoryRelationText
    second: MemoryRelationText


class MemoryRelationProposal(DomainModel):
    """A model's suggestion about two memories. It is never an authority.

    Both spans must be sentences quoted from the memory they name; a proposal without them is
    held instead of applied.
    """

    relation: MemoryRelation
    reason: str = Field(max_length=400)
    first_span: str = Field(default="", max_length=400)
    second_span: str = Field(default="", max_length=400)


RelationOutcome = Literal[
    "MODEL_APPLIED",
    "MODEL_UNDECIDED",
    "HELD_NO_MODEL",
    "HELD_OVER_LIMIT",
    "HELD_NO_EVIDENCE",
    "HELD_MODEL_FAILED",
]


class MemoryRelationJudgment(DomainModel):
    """What happened to one relation the rules could not settle."""

    memory_id: str
    other_memory_id: str
    order: Literal["CANDIDATE_FIRST", "EXISTING_FIRST"]
    outcome: RelationOutcome
    proposed_relation: MemoryRelation | None = None
    reason: str | None = None
    authority: Literal["PROPOSAL_ONLY"] = "PROPOSAL_ONLY"
