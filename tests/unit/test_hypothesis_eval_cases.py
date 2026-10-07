"""The synthetic reason-tag cases: the stored verdicts are what the server rule computes.

No model is called. The web tests read the same `cases.json` for tags and next-check wording.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.fixtures.hypothesis_eval.case_builder import FILE, NOTICE, view_of_case

DOCUMENT = json.loads(FILE.read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = DOCUMENT["cases"]
REQUIRED = {
    "fog-no-result",
    "rain-valid-miss",
    "rain-miss-and-fog-untested",
    "dry-pass",
    "rain-result-edited",
    "unit-mismatch",
    "denominator-zero",
    "value-disagrees-with-counts",
    "condition-mismatch",
    "no-rule",
    "two-conditions-miss",
}


def _verdict(view: dict[str, Any], kind: str, subject: str) -> dict[str, Any]:
    found = [
        v for v in view["verdicts"] if v["subject_kind"] == kind and v["subject_id"] == subject
    ]
    assert len(found) == 1, (kind, subject)
    return found[0]


def test_the_file_is_synthetic_and_covers_the_agreed_situations() -> None:
    assert (
        DOCUMENT["schema"] == "hypothesis-eval-cases/1" and DOCUMENT["synthetic_notice"] == NOTICE
    )
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)) and set(ids) >= REQUIRED and len(ids) >= 10
    for case in CASES:
        assert case["synthetic_notice"] == NOTICE and case["banned_phrases"]
        for item in case["view"]["items"]:
            assert item["fields"]["synthetic_notice"] == NOTICE


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_stored_view_is_what_the_rule_computes(case: dict[str, Any]) -> None:
    computed = json.loads(json.dumps(view_of_case(case)))
    assert computed == case["view"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_states_and_reason_codes_match_the_answer_key(case: dict[str, Any]) -> None:
    view = view_of_case(case)
    for check in case["checks"]:
        verdict = _verdict(view, check["kind"], check["subject_id"])
        assert verdict["state"] == check["state"], check
        heads = {code.split(":")[0] for code in verdict["reasons"]["computed"]}
        assert heads == set(check["reason_codes"]), check
        assert (verdict["currentness"]["state"] == "STALE_BASIS") == check["stale"], check


def test_no_pass_stands_without_a_chosen_result_and_every_chosen_result_has_its_source() -> None:
    passes = grounded = chosen_total = 0
    for case in CASES:
        for verdict in case["view"]["verdicts"]:
            if verdict["subject_kind"] != "CRITERION":
                continue
            chosen = verdict["selection"]["chosen"] if verdict["selection"] else []
            sources = verdict["basis"]["source_span_refs"]
            if chosen:
                chosen_total += 1
                grounded += bool(sources)
            if verdict["state"] == "PASS_COMPUTED":
                passes += 1
                assert chosen and sources, (case["id"], verdict["subject_id"])
    assert passes >= 3 and chosen_total >= 8
    assert grounded == chosen_total
