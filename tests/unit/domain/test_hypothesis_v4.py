"""Unknown is a state; language and legacy sentinel strings are not new state owners."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from tests.unit.application.test_hypothesis_table_filler import portfolio

from thoth.application.services.hypothesis_contract_v3 import settle_contract
from thoth.application.services.hypothesis_projection import full_hypothesis, hypothesis_view
from thoth.application.services.hypothesis_table_filler import count_cells, merge_fill
from thoth.domain.canonical import canonical_payload
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.hypothesis_v4 import (
    ExpectedResultV4,
    HypothesisPortfolioV4,
    HypothesisTableFillV4,
)
from thoth.domain.research_execution import research_work
from thoth.domain.research_projection import HypothesisGenerationDetails
from thoth.domain.research_reference import RevisionRef


def v4():
    value = portfolio({"h1": {"h1": "모름", "h2": "모름"}}).model_dump()
    rows = value["hypotheses"][0]["discriminating_tests"][0]["expected_by_hypothesis"]
    rows[0].update(knowledge_state="PREDICTED")
    rows[1].update(knowledge_state="UNKNOWN", expected=None, basis=["span:0"])
    return HypothesisPortfolioV4.model_validate(value)


@pytest.mark.parametrize(
    "state,text", [("UNKNOWN", "Unknown"), ("PREDICTED", None), ("PREDICTED", " ")]
)
def test_contradictory_state_is_rejected(state, text):
    with pytest.raises(ValidationError):
        ExpectedResultV4(hypothesis_id="h1", knowledge_state=state, expected=text)


def test_unknown_is_open_but_predicted_sentinel_is_a_prediction():
    initial = v4()
    assert count_cells(initial) == (9, 8)
    settled = settle_contract(initial, (), "")
    # Evidence refs are validated; an unknown with an unknown span is dropped, not fabricated.
    assert len(settled.hypotheses[0].discriminating_tests[0].expected_by_hypothesis) == 1
    fill = HypothesisTableFillV4.model_validate(
        {
            "tests": [
                {
                    "designed_for": "h1",
                    "test_id": "t-h1",
                    "rows": [
                        {
                            "hypothesis_id": "h2",
                            "knowledge_state": "PREDICTED",
                            "expected": "stays slow",
                            "basis": ["span:0"],
                        },
                        {
                            "hypothesis_id": "h3",
                            "knowledge_state": "UNKNOWN",
                            "expected": None,
                            "unknown_reason": "Conditions are missing",
                        },
                    ],
                }
            ]
        }
    )
    merged, changed, answers = merge_fill(initial, fill, {"span:0"}, "")
    assert changed == 1
    rows = merged.hypotheses[0].discriminating_tests[0].expected_by_hypothesis
    assert rows[0].expected == "모름" and rows[0].knowledge_state == "PREDICTED"
    assert rows[1].expected == "stays slow" and rows[1].basis == ("span:0",)
    assert rows[2].expected is None and rows[2].knowledge_state == "UNKNOWN"
    assert {answer.outcome for answer in answers} == {"CHANGED", "STILL_UNKNOWN", "NOT_ANSWERED"}


def test_v4_canonical_round_trip_preserves_origin_unknown_basis_and_prediction():
    ref = RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="r",
        revision_digest="a" * 64,
    )
    token = research_work.set(SimpleNamespace(request_ref=ref, response_language="en", context={}))
    try:
        record = full_hypothesis(
            v4().hypotheses[0],
            project_id="p",
            portfolio_id="portfolio:o",
            revision_id="r:h1",
            created_at=datetime(2026, 10, 12, tzinfo=UTC),
            parent=None,
        )
        assert record.schema_version == "1.2.0"
        stored = canonical_payload(record)
        restored = HypothesisRecord.model_validate_json(stored)
        assert canonical_payload(restored) == stored
        assert restored.generation_details.generating_request_ref == ref
        assert restored.generation_details.response_language == "en"
        # The unchanged pre-v4 nested codec fails closed, even before considering table values.
        with pytest.raises(ValidationError):
            HypothesisGenerationDetails.model_validate(restored.generation_details.model_dump())
        with pytest.raises(ValidationError, match="HYPOTHESIS_RECORD_CODEC_MISMATCH"):
            HypothesisRecord.model_validate({**restored.model_dump(), "schema_version": "1.1.0"})
        with pytest.raises(ValidationError, match="HYPOTHESIS_RECORD_VERSION_UNSUPPORTED"):
            HypothesisRecord.model_validate({**restored.model_dump(), "schema_version": "9.0.0"})
        rows = hypothesis_view(restored).discriminating_tests[0].expected_by_hypothesis
        assert rows[0].knowledge_state == "PREDICTED" and rows[0].expected == "모름"
        assert rows[1].knowledge_state == "UNKNOWN" and rows[1].basis == ("span:0",)
    finally:
        research_work.reset(token)
