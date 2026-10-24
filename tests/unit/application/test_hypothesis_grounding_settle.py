"""Malformed grounding and ambiguous IDs affect their own rows/groups only."""

from copy import deepcopy

from tests.fixtures.hypothesis_grounding_wire import raw_portfolio, wire_context

from thoth.application.services.hypothesis_grounding_settle import settle_grounded_portfolio
from thoth.domain.hypothesis_v5_wire import ReceivedHypothesisPortfolioV5


def test_invalid_grounding_drops_one_row_and_retains_valid_neighbors() -> None:
    data = raw_portfolio({"h1": {"h1": "worse", "h2": "same", "h3": "same"}}).model_dump(
        mode="json"
    )
    rows = data["hypotheses"][0]["discriminating_tests"][0]["expected_by_hypothesis"]
    rows[1]["grounding"] = {"derivation_role": "EVIDENCE_INFERENCE", "rationale": "A premise."}
    received = ReceivedHypothesisPortfolioV5.model_validate(data)
    result = settle_grounded_portfolio(received, wire_context(), None)
    assert [
        r.hypothesis_id
        for r in result.portfolio.hypotheses[0].discriminating_tests[0].expected_by_hypothesis
    ] == ["h1", "h3"]
    assert result.diagnostics[0].reason == "GROUNDING_ROLE_BASIS_CONFLICT"
    assert result.diagnostics[0].original_label_chars == 4
    assert "same" not in result.diagnostics[0].model_dump_json()


def test_duplicate_targets_drop_all_duplicates_not_just_later_rows() -> None:
    data = raw_portfolio({"h1": {"h1": "worse", "h2": "same", "h3": "same"}}).model_dump(
        mode="json"
    )
    rows = data["hypotheses"][0]["discriminating_tests"][0]["expected_by_hypothesis"]
    rows.append(deepcopy(rows[1]))
    result = settle_grounded_portfolio(
        ReceivedHypothesisPortfolioV5.model_validate(data), wire_context(), None
    )
    assert len(result.diagnostics) == 2
    assert all(d.reason == "DUPLICATE_HYPOTHESIS" for d in result.diagnostics)
    assert [
        r.hypothesis_id
        for r in result.portfolio.hypotheses[0].discriminating_tests[0].expected_by_hypothesis
    ] == ["h1", "h3"]


def test_duplicate_tests_leave_other_owners_untouched() -> None:
    data = raw_portfolio({"h1": {"h1": "worse"}, "h2": {"h2": "worse"}}).model_dump(mode="json")
    tests = data["hypotheses"][0]["discriminating_tests"]
    tests.append(deepcopy(tests[0]))
    result = settle_grounded_portfolio(
        ReceivedHypothesisPortfolioV5.model_validate(data), wire_context(), None
    )
    assert result.portfolio.hypotheses[0].discriminating_tests == ()
    assert len(result.portfolio.hypotheses[1].discriminating_tests) == 1
    assert all(d.reason == "DUPLICATE_TEST_ID" for d in result.diagnostics)


def test_no_evidence_does_not_force_unknown_and_bad_schema_drops_only_its_row() -> None:
    data = raw_portfolio({"h1": {"h1": "worse", "h2": "same"}}).model_dump(mode="json")
    rows = data["hypotheses"][0]["discriminating_tests"][0]["expected_by_hypothesis"]
    rows[1]["grounding"]["schema_version"] = "unknown"
    result = settle_grounded_portfolio(
        ReceivedHypothesisPortfolioV5.model_validate(data), wire_context(), None
    )
    kept = result.portfolio.hypotheses[0].discriminating_tests[0].expected_by_hypothesis
    assert len(kept) == 1 and kept[0].knowledge_state == "PREDICTED"
    assert kept[0].basis == () and kept[0].grounding.rationale == ""


def test_rationale_uses_response_language_without_modifying_the_source() -> None:
    received = raw_portfolio({"h1": {"h1": "worse", "h2": "same"}})
    data = received.model_dump(mode="json")
    row = data["hypotheses"][0]["discriminating_tests"][0]["expected_by_hypothesis"][1]
    row.update(
        basis=["span:0"],
        grounding={"derivation_role": "EVIDENCE_INFERENCE", "rationale": "한글 설명"},
    )
    result = settle_grounded_portfolio(
        ReceivedHypothesisPortfolioV5.model_validate(data), wire_context(), "en"
    )
    assert result.diagnostics[0].reason == "GROUNDING_RATIONALE_LANGUAGE_MISMATCH"
    assert received.hypotheses[0].discriminating_tests[0].expected_by_hypothesis[1].basis == ()
