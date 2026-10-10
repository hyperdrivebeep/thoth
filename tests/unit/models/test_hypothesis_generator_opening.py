"""The v3 generator contract calls a hypothesis a candidate to check, not a claim to justify.

Only the v3 prompt version changes the two opening sentences of the generator's base contract;
the v2 text and the text without a version stay as they always were.
"""

from __future__ import annotations

from thoth.adapters.models.codex_oauth import role_contract

V2_OPENING = (
    "Create only justified source-grounded hypotheses. Zero or one is allowed when "
    "alternatives_considered, next_checks and uncertainty_reserve explain the limitation. "
    "Causal locus may be null for predictive or exploratory intent. Drafts may have gaps. "
    "Every hypothesis needs support refs or explicit missing evidence, a "
    "counterevidence query, predicted observations, and a discriminating test. Use only "
    "provided span IDs and copy input_head_set_digest exactly. "
)
V3 = "hypothesis_portfolio.v3.3"
V2 = "hypothesis_portfolio.v2"


def test_the_v2_and_unversioned_generator_text_keep_every_character_of_the_old_opening() -> None:
    for version in ("", V2, "something_else"):
        assert role_contract("HYPOTHESIS_GENERATOR", version).startswith(V2_OPENING)


def test_the_v3_opening_asks_for_candidates_and_never_calls_missing_evidence_a_reason_to_drop() -> (
    None
):
    text = role_contract("HYPOTHESIS_GENERATOR", V3)
    # The old exit is gone from the v3 opening.
    assert "Create only justified source-grounded hypotheses" not in text
    assert "Zero or one is allowed when" not in text
    # A hypothesis is a candidate to check; thin evidence still gets a DRAFT, gaps go in
    # missing_evidence, and a lack of evidence is never a reason to leave a candidate out.
    assert "a candidate to examine, not a claim" in text
    assert "DRAFT hypothesis" in text and "usually three to five" in text
    assert "missing_evidence" in text
    assert "never a reason to leave a hypothesis out" in text
    # Few hypotheses stay possible when there really are few; alternatives are for non-candidates.
    assert "only when at most one plausible candidate exists" in text
    assert "only candidates that make no sense, fall outside the question, or duplicate" in text
    # The rules that stay are still there.
    assert "Every hypothesis needs support refs or explicit missing evidence" in text
    assert "copy input_head_set_digest exactly" in text


def test_the_hanja_ban_is_part_of_the_v3_contract_only() -> None:
    v3 = role_contract("HYPOTHESIS_GENERATOR", V3)
    assert "Hangul" in v3 and "Chinese characters" in v3 and "hanja" in v3
    for version in ("", V2):
        assert "hanja" not in role_contract("HYPOTHESIS_GENERATOR", version)
