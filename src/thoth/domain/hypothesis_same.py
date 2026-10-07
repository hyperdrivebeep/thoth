"""Hypotheses a person marked as the same one across investigations.

Asking the same question of the same trace row again makes new hypothesis ids, so what was found
about the earlier ones does not meet the new ones. Only a person says two of them are the same
hypothesis, and may take that back; nothing is paired by how the sentences read. A mark is an
appended event (LINK or UNLINK). A current link is a pair that was linked and not unlinked since,
and a group is everything joined by current links. The groups are a reference: they are read to
show earlier results and to widen a lesson's refutation, and they never merge counts, rankings or
the elimination of a hypothesis.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel

SameAction = Literal["LINK", "UNLINK"]
HypothesisKey = Annotated[str, Field(min_length=1, max_length=200)]


class SameHypothesisEvent(DomainModel):
    event_id: str = Field(min_length=1)
    hypothesis_ids: tuple[HypothesisKey, HypothesisKey]
    action: SameAction
    note: str = Field(default="", max_length=500)
    actor_id: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _two_different_stored_in_order(self) -> Self:
        first, second = self.hypothesis_ids
        if first == second:
            raise ValueError("SAME_IDENTICAL")
        if first > second:
            object.__setattr__(self, "hypothesis_ids", (second, first))
        return self


class SameHypothesisRecord(DomainModel):
    """Every mark of the project, oldest first; a write adds one and removes none."""

    record_kind: Literal["SameHypothesisRecord"] = "SameHypothesisRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"  # the research record codec reads this version
    project_id: str
    events: tuple[SameHypothesisEvent, ...] = ()


def current_pairs(record: SameHypothesisRecord) -> set[tuple[str, str]]:
    """The pairs that are linked now: the latest mark of a pair decides."""
    pairs: set[tuple[str, str]] = set()
    for item in record.events:
        if item.action == "LINK":
            pairs.add(item.hypothesis_ids)
        else:
            pairs.discard(item.hypothesis_ids)
    return pairs


def groups(record: SameHypothesisRecord) -> list[frozenset[str]]:
    """The sets of hypotheses joined by current links, each with at least two, in a fixed order."""
    joined: list[set[str]] = []
    for first, second in sorted(current_pairs(record)):
        touching = [group for group in joined if first in group or second in group]
        merged = {first, second}.union(*touching)
        joined = [group for group in joined if group not in touching]
        joined.append(merged)
    return sorted((frozenset(group) for group in joined), key=sorted)


def group_of(record: SameHypothesisRecord, hypothesis_id: str) -> frozenset[str]:
    """The hypotheses marked the same as this one, itself included (just itself when none)."""
    return next(
        (group for group in groups(record) if hypothesis_id in group), frozenset({hypothesis_id})
    )
