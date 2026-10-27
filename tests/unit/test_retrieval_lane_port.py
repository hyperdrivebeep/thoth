"""S2: the shared domain function gives exactly what the research path gave before it moved.

The oracle, expectations, random corpora and pinned digest come from the S1 file, so a change of
rules cannot hide behind a changed expectation. These tests call the domain function directly; the
S1 file keeps calling the research entry point, which is now a shell around the same function.
"""

import pytest
from tests.unit.test_retrieval_selection_oracle import (
    EXPECTED,
    PINNED_DIGEST,
    Scenario,
    build_scenarios,
    o_full_catalog,
    oracle_select,
    random_scenarios,
)

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.retrieval_lane import (
    LANES,
    LEXICAL_SUBSTRING_V1,
    LaneSelection,
    RetrievalRequest,
    select_retrieval_context,
)


def run(sc: Scenario, lane_id: str = LEXICAL_SUBSTRING_V1) -> LaneSelection:
    catalog = None if sc.context is None else sc.context.to_catalog()
    request = RetrievalRequest(sc.question, sc.queries, sc.evidence, sc.pinned, catalog)
    return select_retrieval_context(request, sc.policy, lane_id)


def ids_of(selection: LaneSelection) -> list[str]:
    return [s.span_id for s in selection.selected]


def oracle_of(sc: Scenario) -> list[str]:
    return oracle_select(sc.question, sc.queries, sc.evidence, sc.pinned, sc.context, sc.policy)


@pytest.mark.parametrize("name", list(build_scenarios()))
def test_the_domain_function_equals_the_oracle_and_the_reviewed_expectation(name: str) -> None:
    sc = build_scenarios()[name]
    assert ids_of(run(sc)) == oracle_of(sc) == EXPECTED[name]


def test_random_corpora_select_the_same_ordered_spans_through_the_domain_function() -> None:
    for sc in random_scenarios():
        assert ids_of(run(sc)) == oracle_of(sc)


def test_the_domain_function_reproduces_the_pinned_digest() -> None:
    results = [ids_of(run(sc)) for sc in [*build_scenarios().values(), *random_scenarios()]]
    digest = domain_digest(
        "S1_RETRIEVAL_SELECTION_PIN", "1.0.0", canonical_payload({"results": results})
    )
    assert digest == PINNED_DIGEST


def old_mode(sc: Scenario) -> str:
    eligible = connected_retrieval_spans(sc.evidence)
    if o_full_catalog(eligible, sc.context, sc.policy) is not None:
        return "FULL_ELIGIBLE_EXTRACTED_CATALOG"
    if sc.context is None or not sc.context.all_observed:
        return "LEGACY"
    return "LEXICAL"


def test_the_reported_mode_is_the_one_the_coverage_record_used_to_derive() -> None:
    for sc in [*build_scenarios().values(), *random_scenarios()]:
        selection = run(sc)
        assert selection.mode == old_mode(sc)
        assert selection.eligible == connected_retrieval_spans(sc.evidence)


def test_only_a_context_that_misses_a_span_reports_a_limitation() -> None:
    scenarios = build_scenarios()
    for name, sc in scenarios.items():
        expected = (
            ("CONTEXT_ORDERED_REFS_MISMATCH",)
            if name == "full_catalog_refused_when_context_misses_a_span"
            else ()
        )
        assert run(sc).limitations == expected, name


def test_a_new_lane_is_used_without_changing_the_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    def newest_first(
        request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
    ) -> tuple[EvidenceSpan, ...]:
        del request
        return tuple(reversed(eligible))

    monkeypatch.setitem(LANES, "NEWEST_FIRST_TEST", newest_first)
    sc = build_scenarios()["no_context_uses_the_ranked_path"]
    reversed_ids = [s.span_id for s in reversed(connected_retrieval_spans(sc.evidence))]
    assert ids_of(run(sc, "NEWEST_FIRST_TEST")) == reversed_ids
    assert ids_of(run(sc)) == EXPECTED["no_context_uses_the_ranked_path"]


def test_the_frame_still_owns_pinning_budget_and_the_whole_catalog_for_any_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def everything(
        request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
    ) -> tuple[EvidenceSpan, ...]:
        del request
        return eligible

    monkeypatch.setitem(LANES, "EVERYTHING_TEST", everything)
    pinned = build_scenarios()["pinned_first_and_denied_pins_are_ignored"]
    chosen = ids_of(run(pinned, "EVERYTHING_TEST"))
    assert chosen[0] == "span:low" and len(chosen) == pinned.policy.max_spans
    full = build_scenarios()["full_catalog_in_document_order_of_the_context"]
    assert (
        ids_of(run(full, "EVERYTHING_TEST"))
        == EXPECTED["full_catalog_in_document_order_of_the_context"]
    )


def test_an_unknown_lane_is_refused() -> None:
    with pytest.raises(ValueError, match="RETRIEVAL_LANE_UNKNOWN"):
        run(build_scenarios()["no_evidence"], "NO_SUCH_LANE")
