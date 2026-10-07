"""A test result a person recorded, and when it eliminates a hypothesis.

An observation matching the alternative explanation eliminates the hypothesis within what was
observed: once for one result, repeated for results of two different tests. Nothing is deleted, and
the latest result of a test is the one that counts, so a corrected result changes the state.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.domain.discrimination import (
    DiscriminationLedgerRecord,
    DiscriminationResult,
    MatchKind,
    RefutationConditions,
    elimination,
    latest_results,
    recorded_standing,
)

H = "hypothesis:one"


def result(
    test: str, matched: MatchKind, *, hypothesis: str = H, n: int = 1
) -> DiscriminationResult:
    return DiscriminationResult(
        event_id=f"e{n}",
        hypothesis_id=hypothesis,
        hypothesis_revision_digest="h" * 64,
        test_id=test,
        observation="what was seen",
        matched=matched,
        actor_id="human:local-user",
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
    )


def test_one_result_that_matches_the_alternative_eliminates_once() -> None:
    assert elimination((result("t1", "ALTERNATIVE"),), H) == "SINGLE"


def test_results_of_two_different_tests_make_it_repeated_but_one_test_twice_does_not() -> None:
    both = (result("t1", "ALTERNATIVE"), result("t2", "ALTERNATIVE", n=2))
    assert elimination(both, H) == "REPEATED"
    twice = (result("t1", "ALTERNATIVE"), result("t1", "ALTERNATIVE", n=2))
    assert elimination(twice, H) == "SINGLE"


def test_a_result_that_does_not_match_the_alternative_eliminates_nothing() -> None:
    for kind in ("THIS_HYPOTHESIS", "NEITHER", "UNDETERMINED"):
        assert elimination((result("t1", kind),), H) is None  # type: ignore[arg-type]
    assert elimination((), H) is None


def test_the_latest_result_of_a_test_counts_and_the_earlier_one_stays_recorded() -> None:
    events = (result("t1", "ALTERNATIVE"), result("t1", "THIS_HYPOTHESIS", n=2))
    assert elimination(events, H) is None
    assert latest_results(events, H)["t1"].matched == "THIS_HYPOTHESIS"
    assert len(events) == 2  # the ledger is only ever appended to


def test_results_of_another_hypothesis_do_not_count() -> None:
    other = (result("t1", "ALTERNATIVE", hypothesis="hypothesis:two"),)
    assert elimination(other, H) is None


def test_a_result_needs_words_and_a_known_kind_and_conditions_are_few_and_not_blank() -> None:
    with pytest.raises(ValidationError):
        DiscriminationResult(**{**result("t1", "NEITHER").model_dump(), "observation": ""})
    with pytest.raises(ValidationError):
        DiscriminationResult(**{**result("t1", "NEITHER").model_dump(), "matched": "MAYBE"})
    stamp = datetime(2026, 10, 6, tzinfo=UTC)
    base = dict(event_id="c1", hypothesis_id=H, actor_id="human:a", created_at=stamp)
    assert RefutationConditions(conditions=("one",), **base).conditions == ("one",)
    assert RefutationConditions(conditions=(), **base).conditions == ()  # clearing is a record too
    with pytest.raises(ValidationError):
        RefutationConditions(conditions=("",), **base)
    with pytest.raises(ValidationError):
        RefutationConditions(conditions=tuple(f"c{i}" for i in range(11)), **base)


def test_a_ledger_with_nothing_in_it_is_valid() -> None:
    assert DiscriminationLedgerRecord(project_id="p").results == ()


def test_the_recorded_standing_of_a_hypothesis_follows_its_latest_results_by_a_fixed_table() -> (
    None
):
    this, alt = "THIS_HYPOTHESIS", "ALTERNATIVE"
    table: list[tuple[tuple[tuple[str, str], ...], str]] = [
        ((), "NONE"),
        ((("t1", "NEITHER"),), "NONE"),  # a result that fits neither counts for nothing
        ((("t1", "UNDETERMINED"), ("t2", "NEITHER")), "NONE"),
        ((("t1", this),), "FITS"),
        ((("t1", this), ("t2", this)), "FITS"),
        ((("t1", this), ("t2", "NEITHER")), "FITS"),
        ((("t1", alt),), "AGAINST_ONCE"),
        ((("t1", alt), ("t2", "UNDETERMINED")), "AGAINST_ONCE"),
        ((("t1", alt), ("t2", alt)), "AGAINST_REPEATED"),
        ((("t1", this), ("t2", alt)), "MIXED"),
        ((("t1", alt), ("t2", alt), ("t3", this)), "MIXED"),
    ]
    for number, (results, expected) in enumerate(table):
        events = tuple(result(test, kind, n=n) for n, (test, kind) in enumerate(results, 1))  # type: ignore[arg-type]
        assert recorded_standing(events, H) == expected, (number, results)


def test_a_test_recorded_again_counts_once_and_only_its_latest_result_decides() -> None:
    events = (result("t1", "ALTERNATIVE"), result("t2", "ALTERNATIVE", n=2))
    assert recorded_standing(events, H) == "AGAINST_REPEATED"
    corrected = (*events, result("t2", "THIS_HYPOTHESIS", n=3))
    assert recorded_standing(corrected, H) == "MIXED"
    again = (*corrected, result("t1", "THIS_HYPOTHESIS", n=4))
    assert recorded_standing(again, H) == "FITS"
    twice = (result("t1", "ALTERNATIVE"), result("t1", "ALTERNATIVE", n=2))
    assert recorded_standing(twice, H) == "AGAINST_ONCE"


def test_the_standing_matches_the_elimination_it_is_read_beside_and_ignores_other_hypotheses() -> (
    None
):
    events = (result("t1", "ALTERNATIVE"), result("t2", "ALTERNATIVE", n=2))
    assert (
        elimination(events, H) == "REPEATED" and recorded_standing(events, H) == "AGAINST_REPEATED"
    )
    other = (result("t1", "ALTERNATIVE", hypothesis="hypothesis:two"),)
    assert recorded_standing(other, H) == "NONE"
