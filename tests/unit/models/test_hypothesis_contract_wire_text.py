"""The v3 contract text reaches the model only for the v3 prompt version.

No model is called. The Codex, Claude and xAI adapters build their prompt with `prompt_envelope`
and the OpenAI-compatible adapter calls `role_contract` with the same version, so one check
covers them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from thoth.adapters.models import strict_output_schema
from thoth.adapters.models.codex_oauth import prompt_envelope, role_contract
from thoth.adapters.models.hypothesis_contract_text import hypothesis_contract_addendum
from thoth.application.reducers import SufficiencySignals, assess_information_sufficiency
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis import HypothesisPortfolio, HypothesisPortfolioV3
from thoth.domain.model import ContextPack, ModelRequest

SHA = "b" * 64


def _request(
    version: str, output_model: type[HypothesisPortfolio]
) -> ModelRequest[HypothesisPortfolio]:
    cutoff = datetime(2026, 10, 6, tzinfo=UTC)
    sufficiency = assess_information_sufficiency(
        assessment_id="assessment:v3",
        assessment_revision_id="revision:v3",
        project_id="project:v3",
        target_object_id="object:v3",
        cutoff_at=cutoff,
        decision_question="What explains the miss?",
        criteria=(),
        evidence=(),
        signals=SufficiencySignals(),
        policy_version="policy:1",
        input_head_set_digest=SHA,
    )
    return ModelRequest(
        role=ModelRole.HYPOTHESIS_GENERATOR,
        project_id="project:v3",
        cutoff_at=cutoff,
        context_pack=ContextPack(
            case_id="case:v3",
            project_id="project:v3",
            object_id="object:v3",
            problem="What explains the miss?",
            evidence=(),
            criteria=(),
            sufficiency=sufficiency,
            input_head_set_digest=SHA,
        ),
        output_model=output_model,
        prompt_version=version,
        model_policy_ref="model-policy:v3",
        max_output_tokens=500,
    )


def test_the_v3_text_is_added_only_for_the_v3_prompt_version() -> None:
    v3 = prompt_envelope(_request("hypothesis_portfolio.v3.3", HypothesisPortfolioV3))
    v2 = prompt_envelope(_request("hypothesis_portfolio.v2", HypothesisPortfolio))
    assert "CONTRACT_V3" in v3 and "expected_by_hypothesis" in v3 and "refutation_conditions" in v3
    assert "CONTRACT_V3" not in v2 and "expected_by_hypothesis" not in v2
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""
    assert hypothesis_contract_addendum("") == ""


def test_the_contract_without_a_version_is_the_text_v2_always_had() -> None:
    plain = role_contract("HYPOTHESIS_GENERATOR")
    assert role_contract("HYPOTHESIS_GENERATOR", "hypothesis_portfolio.v2") == plain
    assert "CONTRACT_V3" not in plain
    added = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    assert added and added in role_contract("HYPOTHESIS_GENERATOR", "hypothesis_portfolio.v3.3")


def test_the_v3_text_asks_for_predictions_without_evidence() -> None:
    text = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    # A prediction follows from the hypothesis itself, so it is written without evidence.
    assert "if that hypothesis were true" in text
    assert "even when no supplied evidence supports it" in text
    # Evidence is named only where it supports the prediction, else the list is empty.
    assert "only when they support that prediction" in text and "empty list" in text
    # Every test gets a row for every hypothesis, whichever one the test was designed for.
    assert "whichever hypothesis the test was designed for" in text
    assert "never leave a hypothesis out" in text and "or leave the hypothesis out" not in text
    # A hypothesis the test was not designed for says what follows if it were true,
    # consistently with expected_if_alternative.
    assert "was not designed for" in text and "expected_if_alternative" in text
    # 모름 only when the result cannot be fixed logically; another hypothesis's test is no reason.
    assert "only when, even if that hypothesis were true, the result" in text
    assert "cannot be fixed logically" in text
    assert "never a reason for 모름" in text
    assert "implies nothing about this test" not in text and "gives no basis" not in text
    # Hypotheses that predict the same result share one label.
    assert "same label for both" in text
    # The rules that stay: same wording, short label, no score, no re-judging.
    assert "worded the same for the same result" in text and "80 characters" in text
    assert "Add no probability, score or ranking" in text
    assert "do not re-judge or change any verdict" in text
    assert "up to five short sentences" in text


BEFORE_LENGTH = 1708  # the v3 text before the pair goal and the example were added


def test_the_v3_text_asks_for_pairs_to_be_told_apart_and_shows_one_other_domain_example() -> None:
    text = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    # The goal: every two hypotheses differ on at least one test, without forcing it.
    assert "every two hypotheses of this portfolio" in text
    assert "do not force it" in text and "does not separate them" in text
    # One worked example from another domain, marked as form only and not to be copied.
    assert "Illustration of the form only" in text and "another domain" in text
    assert "never copy its wording" in text
    assert "cache" in text and "lock" in text and "network" in text
    # The question-language rule comes right after the example, whatever language the example is in.
    example_end = text.index("fixes the result of every test")
    language = text.index("in the language of the question")
    assert 0 < language - example_end < 120
    # The example adds a bounded amount of text (about 1,000; the later keep-every-hypothesis
    # sentence adds up to 400, the four-hypothesis split example up to 600, the one-cause and
    # fixed-condition sentences up to 900 and the alternatives sentence up to 600, each checked in
    # its own test) and no word of the demo data.
    assert len(text) - BEFORE_LENGTH <= 3_500
    lowered = text.lower()
    for word in ("radar", "synthetic", "syn-", "탐지", "레이더"):
        assert word not in lowered
    assert re.search(r"\brain\b", lowered) is None
    # Nothing is added for v2.
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""


BEFORE_V3G_LENGTH = 2694  # the v3 text before the keep-every-hypothesis sentence was added


def test_the_pair_goal_is_about_test_choice_and_never_cuts_hypotheses() -> None:
    text = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    # The goal is about which tests to choose, not which hypotheses to keep.
    assert "about which tests to choose, not about which hypotheses to keep" in text
    # Every hypothesis that would have been proposed without this contract stays.
    assert "without this contract" in text
    assert "never reduce their number" in text and "alternatives_considered" in text
    # Two hypotheses that no test can separate are both kept and share a label.
    assert "keep both and give them the same label" in text
    # The sentence follows the pair goal directly and adds a bounded amount of text.
    assert text.index("does not separate them") < text.index("about which tests to choose")
    # This sentence adds about 340; the split example added later adds up to 600 more and the
    # one-cause and fixed-condition sentences up to 900 and the alternatives sentence up to 600
    # (their own tests bound them), so everything since this sentence was written stays within
    # 2,500.
    assert len(text) - BEFORE_V3G_LENGTH <= 2_500
    # Nothing is added for v2.
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""


BEFORE_P1_LENGTH = 3036  # the v3 text before the example became four hypotheses and the split goal


def test_the_example_shows_a_two_and_two_split_and_the_goal_asks_for_one() -> None:
    text = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    # The goal: besides confirming single hypotheses, one test that splits them into like-sized
    # groups, when it can be justified.
    assert "at least one test that splits the hypotheses into groups of similar size" in text
    assert "whichever result comes" in text and "do not force one" in text
    # The example has four hypotheses (another domain), a test that splits two and two, and a
    # test for the fourth, so that together every pair is separated (the goal it teaches).
    assert "H4 disk degradation" in text and "H5" not in text
    assert "splits two and two" in text
    assert "H3 becomes fast; H4 becomes fast" in text
    assert "Test for H4 (repeat with a fresh local disk)" in text
    assert "Together every pair is separated" in text
    assert "share every label" not in text
    # Why the splitting test is worth it is a reason only; it gives no order (the screen's rule
    # computes the order) and does not clash with the ban on ranking.
    assert "at most two candidates whichever way it goes" in text
    assert "narrows the candidates more" in text and "do it first" not in text
    # No ranking words were added: the order is the screen's rule, not the model's.
    assert "Add no probability, score or ranking" in text
    # The change is bounded and nothing is added for v2.
    assert len(text) - BEFORE_P1_LENGTH <= 2_100  # this change 600, the next 900, the last 600
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""


BEFORE_C2_LENGTH = 3532  # the v3 text before the one-cause and fixed-condition sentences


def test_each_column_assumes_its_own_hypothesis_and_the_tests_hold_open_conditions_fixed() -> None:
    text = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    # One cause per column: the other hypotheses are not the explanation, so a second cause that
    # could be present is no reason for 모름.
    assert "Each expected assumes that hypothesis is the explanation" in text
    assert "the other hypotheses of this portfolio are not" in text
    assert "write the result that follows under that assumption" in text
    # A test that leaves a condition open is replaced by one that fixes or also measures it, and
    # a test that changes a setting says on which inputs it runs and what else is measured.
    assert "a condition the test leaves open" in text
    assert "holds that condition fixed or measures it as well" in text
    assert "on which inputs it is run and what is measured alongside" in text
    # Both sit around the existing 모름 sentence without contradicting it: the assumption comes
    # first, the 모름 rule is unchanged, and the test choice follows it.
    assumption = text.index("Each expected assumes that hypothesis is the explanation")
    unknown = text.index("only when, even if that hypothesis were true, the result")
    never = text.index("never a reason for 모름")
    choice = text.index("holds that condition fixed or measures it as well")
    assert assumption < unknown < never < choice < text.index("Choose the tests so that every two")
    assert "cannot be fixed logically" in text
    # The language rule bans kana as well as Chinese characters.
    assert "no Chinese characters (hanja) or kana" in text
    # No new example, no word of the demo data, and a bounded amount of text.
    assert text.count("Illustration of the form only") == 1
    lowered = text.lower()
    for word in ("radar", "synthetic", "syn-", "탐지", "레이더", "detection", "fog"):
        assert word not in lowered
    assert re.search(r"\brain\b", lowered) is None
    assert len(text) - BEFORE_C2_LENGTH <= 1_500  # these sentences up to 900, the next up to 600
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""


def test_the_strict_schema_of_the_v3_form_asks_for_the_new_fields() -> None:
    schema = str(strict_output_schema(HypothesisPortfolioV3))
    assert "expected_by_hypothesis" in schema and "refutation_conditions" in schema
    assert "expected_by_hypothesis" not in str(strict_output_schema(HypothesisPortfolio))


BEFORE_D1_LENGTH = 4098  # the v3 text before the alternatives sentence


def test_the_hypotheses_are_alternatives_and_a_path_is_written_as_its_own_cause() -> None:
    text = hypothesis_contract_addendum("hypothesis_portfolio.v3.3")
    # The portfolio is a set of alternatives: one explanation excludes the others.
    assert "The hypotheses of this portfolio are alternatives to one another" in text
    assert "if one is the explanation, the others are not" in text
    # A hypothesis that is only a path or stage another cause could take is rewritten as a cause
    # of its own, so that the two are real alternatives; it is rewritten, never dropped or merged.
    assert "a path or stage through which another hypothesis's cause could act" in text
    assert "a cause that arises on its own" in text
    assert "never drop or merge it" in text
    # It follows the keep-every-hypothesis sentences and comes before the split-test goal, so it
    # does not read as permission to cut or merge hypotheses.
    keep = text.index("never reduce their number")
    same = text.index("keep both and give them the same label")
    alternatives = text.index("are alternatives to one another")
    assert keep < same < alternatives < text.index("Besides tests that confirm a single hypothesis")
    # No new example, no word of the demo data, and a bounded amount of text.
    assert text.count("Illustration of the form only") == 1
    lowered = text.lower()
    for word in (
        "radar",
        "synthetic",
        "syn-",
        "탐지",
        "레이더",
        "detection",
        "fog",
        "track",
        "weather",
    ):
        assert word not in lowered
    assert re.search(r"\brain\b", lowered) is None
    assert len(text) - BEFORE_D1_LENGTH <= 600
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""
