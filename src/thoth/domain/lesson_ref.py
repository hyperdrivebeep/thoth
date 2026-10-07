"""Lessons: references to what a rule computed or a person recorded, with the condition they held.

A lesson is never a sentence. It names a kind, a coded outcome, the records it rests on and the
context those records were made in (the criterion, the unit, the condition and the rule revision).
Only four kinds exist, and each takes only the sources and outcomes of its own kind, so what a
model wrote (a hypothesis sentence, a reflection) and a fix whose effect was never confirmed
cannot be stored as one. A lesson is recalled for a row only when every part of the context
matches; it leaves the recall when the record it rests on changed (stale) or when a later record in
the same context says the opposite about the same hypothesis (refuted). A person may mark
hypotheses of different investigations as the same one; a lesson is then refuted by a later
opposite result of any hypothesis in that group, and only that: counts and rankings stay per
hypothesis. Nothing is deleted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest

LessonKind = Literal["TEST_RESULT", "ELIMINATION", "EFFECT_CONFIRMED", "HUMAN_CLOSURE"]
SourceKind = Literal["DISCRIMINATION_RESULT", "TRACE_CLOSURE"]
LessonState = Literal["SAME_CONDITION", "STALE", "REFUTED"]

# What each kind may say, and what it may rest on. There is no room for a sentence.
_OUTCOMES: dict[str, frozenset[str]] = {
    "TEST_RESULT": frozenset({"THIS_HYPOTHESIS", "ALTERNATIVE", "NEITHER", "UNDETERMINED"}),
    "ELIMINATION": frozenset({"SINGLE", "REPEATED"}),
    "EFFECT_CONFIRMED": frozenset({"CONFIRMED"}),
    "HUMAN_CLOSURE": frozenset({"HUMAN_CLOSED", "WAIVER_RECORDED", "CONDITION_CHANGED"}),
}
_SOURCES: dict[str, str] = {
    "TEST_RESULT": "DISCRIMINATION_RESULT",
    "ELIMINATION": "DISCRIMINATION_RESULT",
    "EFFECT_CONFIRMED": "TRACE_CLOSURE",
    "HUMAN_CLOSURE": "TRACE_CLOSURE",
}
_OPPOSITE = {"ALTERNATIVE": "THIS_HYPOTHESIS", "THIS_HYPOTHESIS": "ALTERNATIVE"}


class LessonContext(DomainModel):
    """What the row's verdict stood on when the record was made: all of it must match to recall."""

    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=100)
    condition: str | None = Field(default=None, max_length=500)
    rule_id: str | None = Field(default=None, max_length=500)
    rule_revision: int | None = None


class LessonSource(DomainModel):
    record_kind: SourceKind
    record_id: str = Field(min_length=1, max_length=200)


class LessonRef(DomainModel):
    lesson_id: str = Field(min_length=1, max_length=200)
    kind: LessonKind
    outcome: str = Field(min_length=1, max_length=40)
    sources: tuple[LessonSource, ...] = Field(min_length=1, max_length=20)
    fingerprint: str = Field(min_length=1, max_length=200)  # the sources as they stood
    context: LessonContext
    hypothesis_id: str | None = Field(default=None, max_length=200)
    test_id: str | None = Field(default=None, max_length=200)
    actor_id: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _only_what_a_rule_or_a_person_recorded(self) -> Self:
        if self.outcome not in _OUTCOMES[self.kind]:
            raise ValueError("LESSON_OUTCOME_NOT_OF_KIND")
        if any(item.record_kind != _SOURCES[self.kind] for item in self.sources):
            raise ValueError("LESSON_SOURCE_NOT_OF_KIND")
        return self


class LessonReferenceRecord(DomainModel):
    """Every lesson of the project, oldest first; a write adds lessons and removes none."""

    record_kind: Literal["LessonReferenceRecord"] = "LessonReferenceRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    refs: tuple[LessonRef, ...] = ()


def lesson_id_of(kind: str, outcome: str, sources: Sequence[LessonSource]) -> str:
    """The same lesson from the same records always has the same id, so it is added once."""
    payload: dict[str, object] = {
        "kind": kind,
        "outcome": outcome,
        "sources": sorted(f"{item.record_kind}:{item.record_id}" for item in sources),
    }
    return domain_digest("LESSON", "1.0.0", canonical_payload(payload))


def fingerprint_of(parts: Mapping[str, object]) -> str:
    return domain_digest("LESSON_FINGERPRINT", "1.0.0", canonical_payload(parts))


def _claim(ref: LessonRef) -> str | None:
    """What the lesson says about its hypothesis, when it says anything that can be opposed."""
    if ref.hypothesis_id is None:
        return None
    if ref.kind == "ELIMINATION":
        return "ALTERNATIVE"
    return ref.outcome if ref.kind == "TEST_RESULT" and ref.outcome in _OPPOSITE else None


def recall_lessons(
    refs: Sequence[LessonRef],
    current: LessonContext,
    fingerprints: Mapping[str, str | None],
    siblings: Mapping[str, frozenset[str]] | None = None,
) -> list[tuple[LessonRef, LessonState]]:
    """The lessons of this exact context with their state; other contexts are not here at all.

    `fingerprints` holds the fingerprint of each lesson's sources as they stand now (None when
    they cannot be found). `siblings` names, for a hypothesis a person marked as the same as others,
    the whole group. Oldest first. Nothing is read from how often a result happened.
    """
    same = [(at, ref) for at, ref in enumerate(refs) if ref.context == current]
    stale = {ref.lesson_id for _, ref in same if fingerprints.get(ref.lesson_id) != ref.fingerprint}
    found: list[tuple[LessonRef, LessonState]] = []
    groups = siblings or {}
    for at, ref in same:
        if ref.lesson_id in stale:
            found.append((ref, "STALE"))
            continue
        claim = _claim(ref)
        mine = groups.get(ref.hypothesis_id or "", frozenset({ref.hypothesis_id or ""}))
        refuted = claim is not None and any(
            later.hypothesis_id in mine
            and later.kind == "TEST_RESULT"
            and later.outcome == _OPPOSITE[claim]
            and later.lesson_id not in stale
            for index, later in same
            if index > at
        )
        found.append((ref, "REFUTED" if refuted else "SAME_CONDITION"))
    return found
