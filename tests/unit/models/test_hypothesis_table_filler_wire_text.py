"""The table-filler role reaches every adapter through the shared role contract (no model call).

The Codex, Claude and xAI adapters build their prompt with `prompt_envelope` and the
OpenAI-compatible adapter calls `role_contract`, so checking those two covers them. The
generator's contract text is not touched by the new role.
"""

from __future__ import annotations

from datetime import UTC, datetime

from thoth.adapters.models import strict_output_schema
from thoth.adapters.models.codex_oauth import prompt_envelope, role_contract
from thoth.adapters.models.hypothesis_contract_text import (
    hypothesis_contract_addendum,
    table_filler_contract,
)
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis import HypothesisTableFill
from thoth.domain.model import ContextPack, ModelRequest

VERSION = "hypothesis_table_filler.v4"


def _request() -> ModelRequest[HypothesisTableFill]:
    return ModelRequest(
        role=ModelRole.HYPOTHESIS_TABLE_FILLER,
        project_id="p",
        cutoff_at=datetime(2026, 10, 10, tzinfo=UTC),
        context_pack=ContextPack(
            case_id="c",
            project_id="p",
            object_id="o",
            problem="무엇이 원인입니까?",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest="b" * 64,
            research_context={"table_filler": {"hypotheses": [], "tests": []}},
        ),
        output_model=HypothesisTableFill,
        prompt_version=VERSION,
        model_policy_ref="policy:1",
        max_output_tokens=3_000,
    )


def test_the_role_has_its_own_contract_and_the_envelope_carries_it() -> None:
    text = role_contract("HYPOTHESIS_TABLE_FILLER", VERSION)
    assert table_filler_contract() in text
    envelope = prompt_envelope(_request())
    assert "HYPOTHESIS_TABLE_FILLER" in envelope and table_filler_contract() in envelope
    assert "table_filler" in envelope and VERSION in envelope
    # the generator's text is neither added to this role nor changed by it
    assert "CONTRACT_V3" not in envelope
    assert hypothesis_contract_addendum("hypothesis_portfolio.v3.3") not in text
    assert role_contract("HYPOTHESIS_GENERATOR") == role_contract(
        "HYPOTHESIS_GENERATOR", "hypothesis_portfolio.v2"
    )


def test_the_contract_asks_for_cells_only_with_the_lessons_of_the_earlier_checks() -> None:
    text = table_filler_contract()
    # only the open cells, nothing new, nothing rewritten
    assert "open_hypothesis_ids" in text and "Add no test and no hypothesis" in text
    assert "change no sentence or existing cell" in text
    # a prediction follows from the hypothesis; it is not a claim about the data
    assert "if that hypothesis were true" in text
    assert "even when no supplied evidence supports it" in text
    assert "is not a claim about the data" in text
    # a hypothesis the test was not designed for: consistent with expected_if_alternative
    assert "was not designed for" in text and "expected_if_alternative" in text
    # 모름 only when the result cannot be fixed; being another hypothesis's test is never a reason
    assert "only when, even if that hypothesis were true" in text
    assert "never a reason for 모름" in text
    # a 모름 comes with one sentence saying what leaves the result undetermined
    assert "also write unknown_reason: one sentence" in text
    assert "left undetermined even if that hypothesis were true" in text
    assert "use null for unknown_reason with any other answer" in text
    # the same result keeps the label already used in the table
    assert "reuse that label exactly" in text and "never reword it" in text
    # short, in the question's language, no score, no re-judging, only known ids
    assert "80 characters" in text and "Hangul only" in text and "hanja" in text
    assert "Add no probability, score or ranking" in text
    assert "do not re-judge or change any verdict" in text
    assert "Use only hypothesis_id and test_id values that appear in table_filler" in text
    # the illustration is marked as form only and uses another domain
    assert "Illustration of the form only" in text and "never copy its wording" in text


def test_the_contract_has_each_row_assume_its_own_hypothesis_and_may_fill_a_designed_for_row() -> (
    None
):
    text = table_filler_contract()
    # one cause per row: the other hypotheses are not the explanation, whatever else could exist
    assert "Each row assumes that hypothesis is the explanation" in text
    assert "the other hypotheses of this portfolio are not" in text
    assert "write the result that follows under that assumption" in text
    assert "even if another cause could also be present" in text
    # the assumption comes before the 모름 rule, which keeps its wording
    assert text.index("Each row assumes") < text.index("Write expected as 모름 only when")
    assert "only when, even if that hypothesis were true" in text
    # a designed-for row that is open is the result the test predicts for its own hypothesis
    assert "If the designed hypothesis itself is open" in text and "expected_if_true" in text
    # no new example
    assert text.count("Illustration of the form only") == 1


def test_the_form_is_cells_only_and_has_no_generator_field() -> None:
    schema = str(strict_output_schema(HypothesisTableFill))
    for name in ("designed_for", "test_id", "rows", "hypothesis_id", "expected", "basis"):
        assert name in schema
    for name in ("refutation_conditions", "expected_by_hypothesis", "statement", "portfolio_id"):
        assert name not in schema
