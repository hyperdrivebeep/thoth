"""Every role with the shared language rule is told to avoid foreign script (no model call)."""

from __future__ import annotations

from thoth.adapters.models.codex_oauth import role_contract
from thoth.domain.enums import ModelRole

HANGUL_RULE = "for a Korean question write them in Hangul"
SCRIPT_RULE = "do not use Chinese characters or kana that the question or the quoted evidence"
KEPT_RULE = "Do not translate identifiers, quoted evidence, or numeric/table citations."


def test_every_role_with_the_shared_language_rule_is_told_to_avoid_foreign_script() -> None:
    covered = [role for role in ModelRole if role != ModelRole.RESEARCH_PLANNER]
    assert len(covered) >= 6
    for role in covered:
        text = role_contract(role.value)
        assert HANGUL_RULE in text, role
        assert SCRIPT_RULE in text, role
        assert KEPT_RULE in text, role  # identifiers, quotes and citations stay untranslated


def test_the_generator_and_filler_get_it_in_every_prompt_version() -> None:
    for version in ("hypothesis_portfolio.v2", "hypothesis_portfolio.v3.3"):
        text = role_contract("HYPOTHESIS_GENERATOR", version)
        assert HANGUL_RULE in text and SCRIPT_RULE in text
    filler = role_contract("HYPOTHESIS_TABLE_FILLER", "hypothesis_table_filler.v4")
    assert HANGUL_RULE in filler and SCRIPT_RULE in filler


def test_the_rule_is_one_sentence_per_role_and_names_no_data() -> None:
    text = role_contract("SEMANTIC_REVIEWER")
    assert text.count(HANGUL_RULE) == 1
    lowered = text.lower()
    for word in ("radar", "synthetic", "syn-", "탐지", "detection", "rain"):
        assert word not in lowered
