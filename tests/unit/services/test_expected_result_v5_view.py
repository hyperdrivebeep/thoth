"""Legacy projections are unassessed, pinned and unavailable when no original binding exists."""

from copy import deepcopy
from typing import Any

import pytest

from thoth.application.services.expected_result_view import expected_result_view, result_read_view
from thoth.application.services.hypothesis_contract_v3 import generator_contract
from thoth.application.services.hypothesis_grounding_v5 import grounding_generator_contract
from thoth.protocol.jsonrpc import RpcApplicationError


def payload(typed: bool = False) -> dict[str, Any]:
    row = {"hypothesis_id": "h", "expected": "모름", "basis": ["s"]}
    if typed:
        row.update(knowledge_state="UNKNOWN", expected=None)
    return {
        "current_result": {
            "source_refs": ["s"],
            "source_basis": {"s": "version:old:" + "a" * 64},
            "result": {
                "portfolio": {
                    "hypotheses": [{"discriminating_tests": [{"expected_by_hypothesis": [row]}]}]
                }
            },
        }
    }


def first(value: dict[str, Any]) -> dict[str, Any]:
    return value["current_result"]["result"]["portfolio"]["hypotheses"][0]["discriminating_tests"][
        0
    ]["expected_by_hypothesis"][0]


@pytest.mark.parametrize("typed", [False, True])
def test_legacy_role_is_never_inferred_from_basis_and_original_is_unchanged(typed: bool) -> None:
    original = payload(typed)
    sealed = deepcopy(original)
    shown = expected_result_view(original, "v5")
    row = first(shown)
    assert row["knowledge_state"] == "UNKNOWN" and row["expected"] is None
    assert row["grounding"]["derivation_role"] == "UNASSESSED"
    assert row["basis"] == ["s"] and row["source_contract"] == ("v4" if typed else "v3")
    assert row["source_bindings"][0]["source_version_id"] == "version:old"
    assert row["relation_assessment"]["assessment_status"] == "UNASSESSED"
    assert original == sealed and shown["expected_result_view"] == "V5_LEGACY_PROJECTION"


def test_binding_requires_membership_and_original_version_hash() -> None:
    original = payload()
    original["current_result"]["source_refs"] = []
    row = first(expected_result_view(original, "v5"))
    assert row["source_bindings"] == [] and row["binding_availability"] == "UNAVAILABLE"
    original["current_result"]["source_refs"] = ["s"]
    original["current_result"]["source_basis"]["s"] = "bad"
    assert first(expected_result_view(original, "v5"))["binding_availability"] == "UNAVAILABLE"


def test_old_operation_without_manifest_is_unavailable_and_empty_basis_stays_unclassified() -> None:
    original = payload()
    result = original["current_result"]["result"]
    projected = result_read_view({"result": result}, "v5")
    row = first({"current_result": projected})
    assert row["binding_availability"] == "UNAVAILABLE"
    first(original)["basis"] = []
    assert (
        first(expected_result_view(original, "v5"))["grounding"]["derivation_role"] == "UNASSESSED"
    )


def test_v2_absent_table_stays_absent_and_unknown_capability_is_rejected() -> None:
    old = {"current_result": {"result": {"portfolio": {"hypotheses": []}}}}
    assert expected_result_view(old, "v5") == old
    with pytest.raises(RpcApplicationError, match="EXPECTED_RESULT_VIEW_UNSUPPORTED"):
        expected_result_view(old, "v99")


def test_contract_selection_owns_version_and_output_budget_together() -> None:
    v4 = generator_contract(True, "en")
    v5 = grounding_generator_contract()
    assert (v4["prompt_version"], v4["max_output_tokens"]) == ("hypothesis_portfolio.v4", 4000)
    assert (v5["prompt_version"], v5["max_output_tokens"]) == ("hypothesis_portfolio.v5", 8000)
    assert generator_contract(False)["max_output_tokens"] == 4000


def test_empty_v5_still_marks_compatibility_without_fabricating_predictions() -> None:
    original = {
        "current_result": {
            "result": {
                "hypothesis_contract_version": "hypothesis_portfolio.v5",
                "portfolio": {"hypotheses": []},
            }
        }
    }
    assert (
        expected_result_view(original, "v4")["expected_result_view"]
        == "V4_COMPATIBILITY_PROJECTION"
    )
    assert (
        expected_result_view(original, None)["expected_result_view"] == "LEGACY_DISPLAY_PROJECTION"
    )
    assert original["current_result"]["result"]["portfolio"]["hypotheses"] == []


def test_internal_portfolio_roundtrip_preserves_the_explicit_source_type() -> None:
    from tests.fixtures.hypothesis_grounding_wire import raw_portfolio, wire_context

    from thoth.application.services.hypothesis_grounding_settle import settle_grounded_portfolio
    from thoth.domain.hypothesis import portfolio_from_json
    from thoth.domain.hypothesis_v5 import HypothesisPortfolioV5

    source = settle_grounded_portfolio(
        raw_portfolio({"h1": {"h1": "worse"}}), wire_context(), None
    ).portfolio
    restored = portfolio_from_json(source.model_dump(), v3=True, v4=True, source=source)
    assert isinstance(restored, HypothesisPortfolioV5) and restored == source
