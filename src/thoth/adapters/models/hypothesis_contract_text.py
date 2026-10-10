"""The contract text added for hypothesis generation v3 (nothing is added for v2)."""

from __future__ import annotations

V3_VERSION = "hypothesis_portfolio.v3.3"

_V3 = (
    " CONTRACT_V3. For every hypothesis prefer discriminating tests that tell it apart from the "
    "other hypotheses of this portfolio. For each discriminating test fill expected_by_hypothesis "
    "with one item per hypothesis_id of this portfolio, whichever hypothesis the test was "
    "designed for; never leave a hypothesis out, because a test is there to tell the hypotheses "
    "of this portfolio apart. expected is the result that would logically follow for the test if "
    "that hypothesis were true, as a short label of at most 80 characters, worded the same for "
    "the same result: when two hypotheses predict the same result, give them the same label for "
    "both. For a hypothesis the test was not designed for, this is usually that the result the "
    "designed hypothesis predicts does not appear; write it consistently with "
    "expected_if_alternative. Write the prediction even when no supplied evidence supports it; "
    "it is derived from the hypothesis and is not a claim about the data. Each expected assumes "
    "that hypothesis is the explanation and the other hypotheses of this portfolio are not; write "
    "the result that follows under that assumption, even if another cause could also be present. "
    "Write expected as 모름 "
    "only when, even if that hypothesis were true, the result of this test cannot be fixed "
    "logically; that the test was designed for another hypothesis is never a reason for 모름. "
    "If a hypothesis's result would depend on a condition the test leaves open, so that you "
    "would have to write 모름, choose a version of the test that holds that condition fixed or "
    "measures it as well, so that every hypothesis fixes the result; a test that changes a "
    "setting needs this most, so say on which inputs it is run and what is measured alongside. "
    "Choose the tests so that every two hypotheses of this portfolio get different expected "
    "labels on at least one test; when that cannot be justified, do not force it: give the two "
    "the same label, which shows that the test does not separate them. This goal is about "
    "which tests to choose, not about which hypotheses to keep: keep every hypothesis you "
    "would have proposed without this contract, and never reduce their number or move one to "
    "alternatives_considered to make the table easier to fill. If no test can justifiably "
    "separate two hypotheses, keep both and give them the same label. The hypotheses of this "
    "portfolio are alternatives to one another: if one is the explanation, the others are not. "
    "When a hypothesis is only a path or stage through which another hypothesis's cause could "
    "act, rewrite it as a cause that arises on its own, whatever the other causes are, so that "
    "the two are real alternatives; rewrite it, never drop or merge it. Besides tests that "
    "confirm a single hypothesis, include at least one test that splits the hypotheses into "
    "groups of similar size when that can be justified, because such a test leaves the fewest "
    "candidates whichever result comes; do not force one. "
    "basis lists supplied span IDs only when they support that prediction; otherwise basis is an "
    "empty list. Use only hypothesis_id values that appear in this portfolio. "
    "Illustration of the form only, from another domain; never copy its wording or content. "
    "A request is slow; H1 cache misconfiguration, H2 database lock contention, H3 network "
    "latency, H4 disk degradation. Test for H1 (repeat the request with the cache disabled): "
    "H1 becomes fast; H2 stays slow; H3 stays slow; H4 stays slow. Test that splits two and "
    "two (repeat from a replica in another region): H1 stays slow; H2 stays slow; H3 becomes "
    "fast; H4 becomes fast. It leaves at most two candidates whichever way it goes, while the "
    "first can leave three, so it narrows the candidates more. Test for H4 (repeat with a "
    "fresh local disk): H1 stays slow; H2 stays slow; H3 stays slow; H4 becomes fast. "
    "Together every pair is separated. 모름 appears nowhere because each hypothesis fixes "
    "the result of every test. "
    "Write every label and sentence in the language of the question, even though this "
    "illustration is in English; for a Korean question write in Hangul only and use no "
    "Chinese characters (hanja) or kana. For every hypothesis fill refutation_conditions with "
    "up to five short sentences, each saying which result would make you drop that hypothesis "
    "(if this result appears, drop it); use an empty list when none can be justified. These "
    "labels and sentences follow the question language like the other user-facing fields. Add no "
    "probability, score or ranking, and do not re-judge or change any verdict."
)


# In v3 the generator's base contract opens with this instead of "Create only justified
# source-grounded hypotheses. Zero or one is allowed ...": a hypothesis is a candidate to check,
# and thin evidence is a reason to say what is missing, never a reason to leave it out.
_V3_OPENING = (
    "Create the candidate causes to check, grounded in the supplied sources where they allow. "
    "A hypothesis is a candidate to examine, not a claim: when the evidence is thin, still "
    "offer every plausible candidate as a DRAFT hypothesis, usually three to five, and write "
    "what is missing in that hypothesis's missing_evidence. Thin or missing evidence is never "
    "a reason to leave a hypothesis out. Zero or one hypothesis is allowed only when at most "
    "one plausible candidate exists, and then alternatives_considered, next_checks and "
    "uncertainty_reserve must explain the limitation. Put into alternatives_considered only "
    "candidates that make no sense, fall outside the question, or duplicate another "
    "hypothesis. "
)


def hypothesis_opening(prompt_version: str, default: str) -> str:
    """The generator's opening sentences: the v3 text for v3, the given text otherwise."""
    return _V3_OPENING if prompt_version == V3_VERSION else default


def hypothesis_contract_addendum(prompt_version: str) -> str:
    """The extra text for this prompt version; empty for v2 and for every other version."""
    return _V3 if prompt_version == V3_VERSION else ""


# The table-filling call: a second, narrow call after the generator wrote the portfolio. It writes
# only the cells the generator left empty or called 모름, so each one gets the attention a cell
# written beside a whole portfolio does not.
_TABLE_FILLER = (
    "Fill only the open cells of the expected-result tables of this portfolio. table_filler lists "
    "the hypotheses and, for each test, the hypothesis it was designed for (designed_for), its "
    "procedure, expected_if_true, expected_if_alternative, the current table, and "
    "open_hypothesis_ids: the hypotheses whose cell is empty or says 모름. For every test that "
    "has open_hypothesis_ids return one tests item with that designed_for and test_id and one "
    "row for each open hypothesis_id. Add no test and no hypothesis and change no sentence or "
    "existing cell. A row's expected is the result that would logically follow for this test if "
    "that hypothesis were true, as a short label of at most 80 characters. Write it even when no "
    "supplied evidence supports it: it is derived from the hypothesis and is not a claim about "
    "the data. For a hypothesis the test was not designed for, this is usually that the result "
    "the designed hypothesis predicts does not appear; write it consistently with "
    "expected_if_alternative. Each row assumes that hypothesis is the explanation and the other "
    "hypotheses of this portfolio are not; write the result that follows under that assumption, "
    "even if another cause could also be present. If the designed hypothesis itself is open, its "
    "row is the result this test predicts if that hypothesis is true (expected_if_true). "
    "Write expected as 모름 only when, even if that hypothesis were "
    "true, the result of this test cannot be fixed logically, for example when the hypothesis "
    "interacts with the test condition so that either result may appear; that the test was "
    "designed for another hypothesis is never a reason for 모름. When you write 모름, also write "
    "unknown_reason: one sentence, in the language of the question, saying under which condition "
    "the result of this test is left undetermined even if that hypothesis were true; use null for "
    "unknown_reason with any other answer. When a hypothesis predicts a "
    "result that a label already in this test's table names, reuse that label exactly and never "
    "reword it: another wording would make two hypotheses look different when they predict the "
    "same result. basis lists supplied span IDs only when they support the prediction; "
    "otherwise it is an empty list. Use only hypothesis_id and test_id values that appear in "
    "table_filler. Illustration of the form only, from another domain; never copy its wording "
    "or content. Test t1 (repeat the request with the cache disabled) was designed for H1 and "
    "its table says H1 becomes fast; open: H2, H3. H2 and H3 are not the designed hypothesis, "
    "so the result H1 predicts does not appear: both rows say stays slow, worded the same. "
    "Write every label in the language of the question, even though this illustration is in "
    "English; for a Korean question write in Hangul only and use no Chinese characters (hanja) "
    "or kana. Add no probability, score or ranking, and do not re-judge or change any verdict."
)


def table_filler_contract() -> str:
    return _TABLE_FILLER
