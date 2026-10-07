"""Lesson recall cases: same or other condition, stale, refuted, and what is never a lesson.

No model is called. The expected states are what the rule gives for synthetic lessons, in
tests/fixtures/hypothesis_eval/lesson_cases.json (a separate file from the reason-tag cases).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from thoth.domain.lesson_ref import LessonContext, LessonRef, recall_lessons

FILE = Path(__file__).resolve().parents[1] / "fixtures" / "hypothesis_eval" / "lesson_cases.json"
DOCUMENT = json.loads(FILE.read_text(encoding="utf-8"))
SOURCES = {
    "TEST_RESULT": "DISCRIMINATION_RESULT",
    "ELIMINATION": "DISCRIMINATION_RESULT",
    "EFFECT_CONFIRMED": "TRACE_CLOSURE",
    "HUMAN_CLOSURE": "TRACE_CLOSURE",
}
REQUIRED = {
    "lesson-same-condition",
    "lesson-other-condition",
    "lesson-stale-fingerprint",
    "lesson-refuted",
}


def ref_of(item: dict[str, Any]) -> LessonRef:
    return LessonRef(
        **{
            "sources": [
                {
                    "record_kind": SOURCES.get(item["kind"], "DISCRIMINATION_RESULT"),
                    "record_id": "src-" + item["lesson_id"],
                }
            ],
            "actor_id": "human:local-user",
            "created_at": datetime(2026, 10, 6, tzinfo=UTC),
            **item,
        }
    )


def test_the_file_is_synthetic_and_covers_the_agreed_situations() -> None:
    assert DOCUMENT["schema_version"] == "hypothesis-lesson-cases/1"
    assert "SYNTHETIC" in DOCUMENT["notice"]
    ids = [case["id"] for case in DOCUMENT["cases"]]
    assert len(ids) == len(set(ids)) and set(ids) >= REQUIRED
    assert all(len(case["about"]) > 20 for case in DOCUMENT["cases"])


@pytest.mark.parametrize("case", DOCUMENT["cases"], ids=lambda case: case["id"])
def test_recall_gives_the_expected_states(case: dict[str, Any]) -> None:
    refs = [ref_of(item) for item in case["refs"]]
    current = LessonContext(**case["current"])
    found = recall_lessons(refs, current, case["fingerprints"])
    assert {ref.lesson_id: state for ref, state in found} == case["expected"]["states"]
    for lesson_id in case["expected"]["absent"]:
        assert all(ref.lesson_id != lesson_id for ref, _ in found)  # not shown; the record stays
    assert recall_lessons(refs, current, case["fingerprints"]) == found  # same input, same answer
    assert len(refs) == len(case["refs"])  # recalling never removes a lesson


@pytest.mark.parametrize("bad", DOCUMENT["invalid_lessons"], ids=lambda bad: bad["about"])
def test_what_is_not_a_rule_or_a_person_s_record_can_never_be_a_lesson(bad: dict[str, Any]) -> None:
    base = {
        "lesson_id": "x",
        "kind": "TEST_RESULT",
        "outcome": "ALTERNATIVE",
        "hypothesis_id": "h1",
        "test_id": "t1",
        "context": DOCUMENT["cases"][0]["current"],
        "fingerprint": "f",
    }
    with pytest.raises(ValidationError):
        ref_of({**base, **bad["patch"]}) if "sources" not in bad["patch"] else LessonRef(
            **{
                "actor_id": "human:a",
                "created_at": datetime(2026, 10, 6, tzinfo=UTC),
                **base,
                **bad["patch"],
            }
        )
