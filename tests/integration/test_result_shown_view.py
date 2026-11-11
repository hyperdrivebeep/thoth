"""thread/result/read says how the screen's summary view of a result relates to the stored one."""

import copy
import json
from pathlib import Path
from typing import Any

from tests.integration.result_evidence_runtime import (
    FIXTURE,
    evidence_read,
    read_case,
    stored_before_grounded_rows,
)

from thoth.application.services.expected_result_view import result_read_view
from thoth.application.services.research_read_summary import summarize_result, summarize_thread_read
from thoth.application.services.result_shown_view import ENVELOPE_KEYS, with_shown_result
from thoth.domain.research_history import HistoricalResultView


def cases() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_the_summary_function_is_the_one_thread_read_uses() -> None:
    stored = cases()["stored_current"]["read"]["manifest"]["result"]
    assert stored["absence_assessments"] != summarize_result(stored)["absence_assessments"]
    payload: dict[str, Any] = {"current_result": {"result": stored, "source_refs": []}}
    summary: Any = summarize_thread_read(payload)
    assert summary["current_result"]["result"] == summarize_result(stored)


def test_a_result_with_no_read_view_has_no_projected_keys_and_keeps_every_other_field() -> None:
    read = copy.deepcopy(cases()["stored_current"]["read"])
    for key in ("shown_result", "projected_keys"):
        read.pop(key)
    shown = with_shown_result(copy.deepcopy(read))
    assert shown["projected_keys"] == [] and shown["shown_result"] == summarize_result(
        read["result"]
    )
    assert {k: v for k, v in shown.items() if k not in ("shown_result", "projected_keys")} == read


def test_only_the_keys_a_read_view_changed_are_named_and_the_manifest_is_left_alone() -> None:
    read = cases()["stored_before_grounded_rows"]["read"]
    assert read["expected_result_view"] == "V5_LEGACY_PROJECTION"
    assert read["projected_keys"] == ["portfolio"]
    stored = read["manifest"]["result"]
    assert {
        k for k in read["result"] if k not in ENVELOPE_KEYS and read["result"][k] != stored.get(k)
    } == {"portfolio"}
    again = copy.deepcopy(read)
    again.pop("expected_result_view")
    assert with_shown_result(again)["projected_keys"] == []  # no declared view, no declared change


def test_the_wrapper_keys_a_published_result_carries_are_not_counted_as_projected() -> None:
    read = copy.deepcopy(cases()["stored_before_grounded_rows"]["read"])
    read["result"] = {
        **read["result"],
        "thread_id": read["thread_id"],
        "request_epoch": 1,
        "contract_version": 2,
        "terminal_reason": "BOUNDED_RESEARCH_COMPLETE",
    }
    assert with_shown_result(read)["projected_keys"] == ["portfolio"]


def test_a_read_view_without_a_manifest_to_compare_with_does_not_claim_to_know_the_keys() -> None:
    read = copy.deepcopy(cases()["stored_before_grounded_rows"]["read"])
    read["manifest"] = None
    assert with_shown_result(read)["projected_keys"] is None
    assert with_shown_result({"result": None, "availability": "UNAVAILABLE"}) == {
        "result": None,
        "availability": "UNAVAILABLE",
    }


def test_the_stored_runtime_answers_are_what_the_server_functions_make_of_them() -> None:
    stored = cases()
    for name, case in stored.items():
        read = case["read"]
        assert read["shown_result"] == summarize_result(read["result"]), name
        assert case["displayed"] == read["shown_result"], name
        view = HistoricalResultView.model_validate(evidence_read(case))
        assert view.selected_evidence is not None and len(view.selected_evidence.items) > 0, name
        assert view.shown_result == read["shown_result"]
    # The older-form case is the current one read through the server's own view functions.
    assert stored["stored_before_grounded_rows"] == stored_before_grounded_rows(
        stored["stored_current"]
    )
    legacy_read = copy.deepcopy(stored["stored_before_grounded_rows"]["read"])
    assert (
        with_shown_result(result_read_view(legacy_read, "v5"))
        == stored["stored_before_grounded_rows"]["read"]
    )


async def test_a_run_of_the_runtime_shows_the_stored_answer_and_the_fixture_is_current(
    tmp_path: Path,
) -> None:
    live = await read_case(tmp_path)
    read = live["read"]
    assert live["displayed"] == read["shown_result"]  # what thread/read shows is shown_result
    assert read["projected_keys"] == [] and read["expected_result_view"] is None
    # The summary replaces the stored record, so a screen cannot compare the two directly.
    assert (
        live["displayed"]["absence_assessments"]
        != read["manifest"]["result"]["absence_assessments"]
    )
    stored = cases()["stored_current"]["read"]
    assert set(read["result"]) == set(stored["result"]), "regenerate the web fixture"
    assert set(read["manifest"]) == set(stored["manifest"])
    assert set(read["shown_result"]) == set(stored["shown_result"])
    for name in ("absence_assessments", "checked_observations"):
        assert set(read["shown_result"][name]) == set(stored["shown_result"][name]), name
