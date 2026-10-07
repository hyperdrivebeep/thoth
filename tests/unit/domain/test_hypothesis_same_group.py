"""Hypotheses a person marked as the same one across investigations, and the groups that makes.

The marks are appended events (LINK, UNLINK). A current link is a pair that was linked and not
unlinked since; a group is everything connected by current links. Nothing is guessed from words.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.domain.hypothesis_same import (
    SameHypothesisEvent,
    SameHypothesisRecord,
    current_pairs,
    group_of,
    groups,
)

STAMP = datetime(2026, 10, 6, tzinfo=UTC)


def event(a: str, b: str, action: str = "LINK", n: int = 1, note: str = "") -> SameHypothesisEvent:
    return SameHypothesisEvent(
        event_id=f"e{n}",
        hypothesis_ids=(a, b),
        action=action,  # type: ignore[arg-type]
        note=note,
        actor_id="human:local-user",
        created_at=STAMP,
    )


def record(*events: SameHypothesisEvent) -> SameHypothesisRecord:
    return SameHypothesisRecord(project_id="p", events=events)


def test_a_pair_is_two_different_hypotheses_stored_in_order() -> None:
    assert event("h2", "h1").hypothesis_ids == ("h1", "h2")
    with pytest.raises(ValidationError):
        event("h1", "h1")
    with pytest.raises(ValidationError):
        SameHypothesisEvent(
            event_id="e",
            hypothesis_ids=("h1",),  # type: ignore[arg-type]
            action="LINK",
            actor_id="human:a",
            created_at=STAMP,
        )
    with pytest.raises(ValidationError):
        event("h1", "h2", note="x" * 501)
    with pytest.raises(ValidationError):
        event("h1", "h2", action="MERGE")


def test_two_linked_hypotheses_make_one_group_and_unlinking_ends_it() -> None:
    linked = record(event("h1", "h2"))
    assert groups(linked) == [frozenset({"h1", "h2"})]
    assert current_pairs(linked) == {("h1", "h2")}
    ended = record(event("h1", "h2"), event("h2", "h1", "UNLINK", n=2))
    assert groups(ended) == [] and current_pairs(ended) == set()
    again = record(*ended.events, event("h1", "h2", n=3))
    assert groups(again) == [frozenset({"h1", "h2"})]
    assert groups(record()) == []


def test_three_or_more_are_one_group_by_their_links_and_an_unlink_can_split_it() -> None:
    chain = record(event("h1", "h2"), event("h2", "h3", n=2), event("h4", "h5", n=3))
    assert sorted(map(sorted, groups(chain))) == [["h1", "h2", "h3"], ["h4", "h5"]]
    assert group_of(chain, "h3") == frozenset({"h1", "h2", "h3"})
    assert group_of(chain, "h9") == frozenset({"h9"})  # alone: only itself
    split = record(*chain.events, event("h2", "h3", "UNLINK", n=4))
    assert sorted(map(sorted, groups(split))) == [["h1", "h2"], ["h4", "h5"]]
    assert group_of(split, "h3") == frozenset({"h3"})
    # a cycle stays one group until enough links are gone
    loop = record(event("h1", "h2"), event("h2", "h3", n=2), event("h1", "h3", n=3))
    assert groups(record(*loop.events, event("h1", "h2", "UNLINK", n=4))) == [
        frozenset({"h1", "h2", "h3"})
    ]
