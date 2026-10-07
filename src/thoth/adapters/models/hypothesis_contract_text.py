"""The contract text added for hypothesis generation v3 (nothing is added for v2)."""

from __future__ import annotations

V3_VERSION = "hypothesis_portfolio.v3"

_V3 = (
    " CONTRACT_V3. For every hypothesis prefer discriminating tests that tell it apart from the "
    "other hypotheses of this portfolio. For each discriminating test fill expected_by_hypothesis "
    "with one item per hypothesis_id of this portfolio: expected is the result that hypothesis "
    "predicts for the test, as a short label of at most 80 characters, worded the same for the "
    "same result; write expected as 모름 when the supplied evidence gives no basis for that "
    "hypothesis, or leave the hypothesis out, and never guess. basis lists only supplied span "
    "IDs that support that expectation and may be empty. Use only hypothesis_id values that "
    "appear in this portfolio. For every hypothesis fill refutation_conditions with up to five "
    "short sentences, each saying which result would make you drop that hypothesis (if this "
    "result appears, drop it); use an empty list when none can be justified. These labels and "
    "sentences follow the question language like the other user-facing fields. Add no "
    "probability, score or ranking, and do not re-judge or change any verdict."
)


def hypothesis_contract_addendum(prompt_version: str) -> str:
    """The extra text for this prompt version; empty for v2 and for every other version."""
    return _V3 if prompt_version == V3_VERSION else ""
