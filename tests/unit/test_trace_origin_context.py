"""A trace origin in the shortlist and the context: what is pinned, and what is never used."""

from typing import Any, cast

from tests.unit.test_research_short_text_selection import source

from thoth.application.services.research_retrieval import lexical_candidates
from thoth.application.services.trace_origin import apply_trace_origin
from thoth.domain.enums import CutoffState
from thoth.domain.research_execution import ResearchWork
from thoth.domain.trace_origin import TraceVerdictOrigin


def origin(**changes: Any) -> dict[str, Any]:
    value = TraceVerdictOrigin(
        project_id="p",
        trace_set_digest="a" * 12,
        subject_kind="CRITERION",
        subject_id="C-1",
        verdict_revision="b" * 64,
        state="HOLD_NO_RESULT",
        reason_codes=("NO_RESULT:weather=rain",),
        source_span_refs=("span:0",),
    ).model_dump(mode="json")
    return {**value, **changes}


def work() -> ResearchWork:
    return ResearchWork(cast(Any, None), "question", cast(Any, None))


def test_a_pinned_span_stays_in_a_full_shortlist_without_widening_it() -> None:
    evidence = source(160)
    plain = lexical_candidates("latency 12 ms alpha", (), evidence)
    assert "span:0" not in {s.span_id for s in plain}  # the question alone does not reach it
    pinned = lexical_candidates("latency 12 ms alpha", (), evidence, ("span:0",))
    ids = [s.span_id for s in pinned]
    assert ids[0] == "span:0"  # kept first
    assert len(pinned) == len(plain) + 1  # only the pin is added to what the question found
    assert set(ids) - {"span:0"} <= {s.span_id for s in plain}


def test_a_pin_that_is_not_retrievable_is_ignored() -> None:
    evidence = list(source(5))
    late = evidence[0].model_copy(
        update={"span_id": "span:late", "cutoff_state": CutoffState.AFTER_CUTOFF}
    )
    evidence.append(late)
    found = lexical_candidates("latency alpha", (), tuple(evidence), ("span:late", "span:none"))
    assert {s.span_id for s in found} == {
        f"span:{i}" for i in range(5)
    }  # eligibility is not bypassed


def test_no_pin_gives_the_same_shortlist_as_before() -> None:
    evidence = source(160)
    assert lexical_candidates("latency 12 ms alpha", (), evidence) == lexical_candidates(
        "latency 12 ms alpha", (), evidence, ()
    )


def test_a_valid_origin_goes_to_the_context_and_pins_its_source_positions() -> None:
    item = work()
    apply_trace_origin(item, {"origin": origin()}, "p")
    assert item.context["origin"]["subject_id"] == "C-1"  # type: ignore[index]
    assert "note" in cast(dict[str, Any], item.context["origin"])
    assert item.pinned_spans == ("span:0",)


def test_no_origin_leaves_the_work_as_it_was() -> None:
    item = work()
    apply_trace_origin(item, {}, "p")
    apply_trace_origin(item, {"origin": None}, "p")
    assert item.context == {} and item.pinned_spans == ()


def test_an_origin_of_another_project_or_in_the_wrong_shape_is_left_out_and_said_so() -> None:
    other = work()
    apply_trace_origin(other, {"origin": origin(project_id="q")}, "p")
    assert "origin" not in other.context and other.pinned_spans == ()
    assert other.context["origin_rejected"] == "TRACE_ORIGIN_PROJECT_MISMATCH"
    made_up = work()
    apply_trace_origin(made_up, {"origin": {"kind": "TRACE_VERDICT", "state": "PASS", "x": 1}}, "p")
    assert "origin" not in made_up.context and made_up.pinned_spans == ()
    assert made_up.context["origin_rejected"] == "TRACE_ORIGIN_INVALID"
