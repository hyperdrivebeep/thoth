"""Preserve the existing open-cell policy with row-level v6 limits and no explanation echo."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from tests.fixtures.hypothesis_grounding_wire import (
    RecordingGroundingModel,
    fill_row,
    filler_payload,
    generator_request,
    raw_portfolio,
    wire_context,
)

from thoth.application.services.hypothesis_grounding_fill import (
    grounding_fill_context,
    merge_grounded_fill,
)
from thoth.application.services.hypothesis_grounding_settle import settle_grounded_portfolio
from thoth.application.services.hypothesis_grounding_v5 import fill_grounded_tables
from thoth.domain.hypothesis_v5_wire import ReceivedHypothesisTableFillV5


@pytest.mark.parametrize("kind", ["long", "basis", "duplicate"])
def test_bad_filler_rows_do_not_remove_a_valid_companion(kind: str) -> None:
    initial = settle_grounded_portfolio(
        raw_portfolio({"h1": {"h1": "worse"}}), wire_context(), None
    )
    bad = fill_row("h2", "x" * 81 if kind == "long" else "same")
    if kind == "basis":
        bad.update(
            basis=[f"span:{i}" for i in range(6)],
            grounding={"derivation_role": "EVIDENCE_INFERENCE", "rationale": "Premise."},
        )
    payload = filler_payload(bad, fill_row("h3"))
    if kind == "duplicate":
        payload["tests"][0]["rows"].append(deepcopy(bad))
    reply = ReceivedHypothesisTableFillV5.model_validate(payload)
    result, changed, answers = merge_grounded_fill(initial, reply, wire_context(), None)
    assert changed == 1
    rows = result.portfolio.hypotheses[0].discriminating_tests[0].expected_by_hypothesis
    assert [r.hypothesis_id for r in rows] == ["h1", "h3"]
    assert result.row_stages[("h1", "t-h1", "h3")] == "FILLER"
    assert result.row_stages[("h1", "t-h1", "h1")] == "GENERATOR"
    assert {a.hypothesis_id: a.outcome for a in answers if a.test_id == "t-h1"} == {
        "h2": "DROPPED",
        "h3": "CHANGED",
    }
    assert result.diagnostics[0].original_label_chars == (81 if kind == "long" else 4)
    if kind == "basis":
        assert result.diagnostics[0].original_basis_count == 6


def test_duplicate_fill_groups_are_all_excluded() -> None:
    initial = settle_grounded_portfolio(
        raw_portfolio({"h1": {"h1": "worse"}}), wire_context(), None
    )
    payload = filler_payload(fill_row("h2"), fill_row("h3"))
    payload["tests"].append(deepcopy(payload["tests"][0]))
    result, changed, _ = merge_grounded_fill(
        initial, ReceivedHypothesisTableFillV5.model_validate(payload), wire_context(), None
    )
    assert changed == 0 and len(result.diagnostics) == 4


def test_normalized_label_is_retained_without_echoing_old_rationale() -> None:
    initial = settle_grounded_portfolio(
        raw_portfolio({"h1": {"h1": "worse"}}), wire_context(), None
    )
    filled, changed, _ = merge_grounded_fill(
        initial,
        ReceivedHypothesisTableFillV5.model_validate(
            filler_payload(fill_row("h2", " " * 5 + "x" * 79))
        ),
        wire_context(),
        None,
    )
    assert changed == 1
    assert (
        len(
            filled.portfolio.hypotheses[0]
            .discriminating_tests[0]
            .expected_by_hypothesis[1]
            .expected
        )
        == 79
    )
    context = grounding_fill_context(wire_context(), filled)
    serialized = context.model_dump_json()
    assert '"rationale"' not in serialized and '"grounding"' not in serialized


@pytest.mark.asyncio
async def test_filler_call_count_is_one_for_open_values_and_zero_for_full_table() -> None:
    context = wire_context()
    request = generator_request()
    command = SimpleNamespace(
        cutoff_at=request.cutoff_at, model_policy_ref=request.model_policy_ref
    )
    model = RecordingGroundingModel()
    initial = settle_grounded_portfolio(raw_portfolio({"h1": {"h1": "worse"}}), context, None)
    result = await fill_grounded_tables(model, initial, context, command)
    assert len(model.calls) == 1 and model.calls[0].max_output_tokens == 3000
    assert result.portfolio.table_fill.contract_version == "hypothesis_table_filler.v6"
    full = {
        owner: {target: "same" for target in ("h1", "h2", "h3")} for owner in ("h1", "h2", "h3")
    }
    final = settle_grounded_portfolio(raw_portfolio(full), context, None)
    await fill_grounded_tables(model, final, context, command)
    assert len(model.calls) == 1
