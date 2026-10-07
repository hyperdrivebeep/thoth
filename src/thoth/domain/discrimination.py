"""Test results and refutation conditions a person records for a hypothesis.

Both are kept apart from the hypothesis, as appended events, so what the model produced is never
edited and nothing a person wrote is overwritten: the latest result of a test is the one that
counts, and the earlier ones stay in the ledger. An observation that matches the alternative
explanation eliminates the hypothesis within what was observed (never a deletion): once for one
result, repeated for results of two different tests.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel

MatchKind = Literal["THIS_HYPOTHESIS", "ALTERNATIVE", "NEITHER", "UNDETERMINED"]
EliminationState = Literal["SINGLE", "REPEATED"]
RecordedStanding = Literal["NONE", "FITS", "AGAINST_ONCE", "AGAINST_REPEATED", "MIXED"]
MAX_CONDITIONS = 10


class DiscriminationResult(DomainModel):
    """What was observed when one discriminating test was done, and which explanation it fit."""

    event_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1, max_length=200)
    hypothesis_revision_digest: str = Field(min_length=64, max_length=64)
    test_id: str = Field(min_length=1, max_length=200)
    observation: str = Field(min_length=1, max_length=2_000)
    matched: MatchKind
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)
    actor_id: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime


class RefutationConditions(DomainModel):
    """What would show the hypothesis wrong, as the person wrote it (empty clears the list)."""

    event_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1, max_length=200)
    conditions: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(
        default=(), max_length=MAX_CONDITIONS
    )
    actor_id: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime


class DiscriminationLedgerRecord(DomainModel):
    """Every result and condition list of the project, oldest first; a write adds, never removes."""

    record_kind: Literal["DiscriminationLedgerRecord"] = "DiscriminationLedgerRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    results: tuple[DiscriminationResult, ...] = ()
    conditions: tuple[RefutationConditions, ...] = ()


def latest_results(
    events: tuple[DiscriminationResult, ...], hypothesis_id: str
) -> dict[str, DiscriminationResult]:
    """The result that counts for each test of this hypothesis: the last one recorded."""
    found: dict[str, DiscriminationResult] = {}
    for item in events:
        if item.hypothesis_id == hypothesis_id:
            found[item.test_id] = item
    return found


def elimination(
    events: tuple[DiscriminationResult, ...], hypothesis_id: str
) -> EliminationState | None:
    """SINGLE for one test whose result fits the alternative, REPEATED for two different tests."""
    fitting = sum(
        1
        for item in latest_results(events, hypothesis_id).values()
        if item.matched == "ALTERNATIVE"
    )
    if fitting == 0:
        return None
    return "REPEATED" if fitting >= 2 else "SINGLE"


def recorded_standing(
    events: tuple[DiscriminationResult, ...], hypothesis_id: str
) -> RecordedStanding:
    """Where the recorded test results leave this hypothesis, read when asked and never stored.

    Only the latest result of each test counts, and only a result that fits this hypothesis or the
    alternative says anything: neither and undetermined are not counted. This is a reading of what
    a person recorded, kept apart from the sealed-test appraisal of the hypothesis (that one is
    not touched), and it changes nothing.
    """
    kinds = {item.matched for item in latest_results(events, hypothesis_id).values()}
    fits, against = "THIS_HYPOTHESIS" in kinds, "ALTERNATIVE" in kinds
    if fits and against:
        return "MIXED"
    if fits:
        return "FITS"
    if against:
        return (
            "AGAINST_REPEATED"
            if elimination(events, hypothesis_id) == "REPEATED"
            else "AGAINST_ONCE"
        )
    return "NONE"
