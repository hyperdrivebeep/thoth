"""History items say what changed in words, and flag changes a reader must see as text."""

from __future__ import annotations

from thoth.application.services.history_change_summary import (
    summarize_change,
    summarize_user_correction,
)

BEFORE = {
    "statement": "조건 alpha에서 지연이 12 ms 늘어난다",
    "evidence_refs": ["span:1", "span:2", "span:3"],
    "counterevidence_refs": [],
    "empirical_appraisal": "UNASSESSED",
    "authority_status": "INFORMAL",
    "cutoff_at": "2026-09-01T00:00:00Z",
    "freshness": "CURRENT",
}


def changed(**patch: object):  # type: ignore[no-untyped-def]
    return summarize_change(BEFORE, {**BEFORE, **patch})


def test_no_change_has_no_summary() -> None:
    assert summarize_change(BEFORE, dict(BEFORE)) is None


def test_a_reworded_statement_is_shown_before_and_after_without_a_forced_text_diff() -> None:
    summary = changed(statement="조건 alpha에서 지연이 12 ms 커진다")
    assert summary is not None
    (line,) = summary.lines
    assert line.label == "가설 문장" and "늘어난다" in line.before and "커진다" in line.after
    assert summary.flags == () and summary.text_diff_recommended is False


def test_a_changed_number_asks_for_the_text_diff() -> None:
    summary = changed(statement="조건 alpha에서 지연이 15 ms 늘어난다")
    assert summary is not None and summary.flags == ("NUMBER",) and summary.text_diff_recommended


def test_a_flipped_negation_asks_for_the_text_diff() -> None:
    summary = changed(statement="조건 alpha에서 지연이 12 ms 늘어나지 않는다")
    assert summary is not None and "NEGATION" in summary.flags and summary.text_diff_recommended


def test_evidence_links_are_counted_with_additions_and_removals() -> None:
    summary = changed(evidence_refs=["span:1", "span:4"])
    assert summary is not None and summary.flags == ("CITATION",)
    (line,) = summary.lines
    assert line.label == "뒷받침 근거"
    assert (line.before, line.after) == ("3개", "2개 (추가 1 · 삭제 2)")


def test_status_authority_and_time_changes_are_flagged() -> None:
    assert (changed(empirical_appraisal="SUPPORTED") or object()).flags == ("STATUS",)  # type: ignore[attr-defined]
    assert (changed(authority_status="OFFICIAL") or object()).flags == ("AUTHORITY",)  # type: ignore[attr-defined]
    assert (changed(cutoff_at="2026-09-15T00:00:00Z") or object()).flags == ("TIME",)  # type: ignore[attr-defined]


def test_an_unlisted_field_is_counted_as_other_and_its_raw_name_stays_in_technical_info() -> None:
    summary = summarize_change({"scope_note": "a"}, {"scope_note": "b"})
    assert summary is not None and summary.flags == () and summary.lines == ()
    assert summary.other == 1
    assert [(line.label, line.before, line.after) for line in summary.technical] == [
        ("scope_note", "a", "b")
    ]
    # identity and timestamps change on every revision and are not a change in meaning
    assert (
        summarize_change(
            {"revision_digest": "a", "created_at": "x"}, {"revision_digest": "b", "created_at": "y"}
        )
        is None
    )


def test_a_long_statement_change_keeps_its_full_text_for_reading() -> None:
    long = "가" * 500
    summary = changed(statement=long)
    assert summary is not None and summary.lines[0].after == long


def test_long_lists_of_changes_are_capped_and_counted() -> None:
    fields = ("statement", "observed_problem", "specification", "risk_tier", "scope", "assertion")
    fields += ("decision_state", "authorization_state", "transition", "support_status")
    fields += ("authority_status", "empirical_appraisal")
    summary = summarize_change({name: "a" for name in fields}, {name: "b" for name in fields})
    assert summary is not None and len(summary.lines) == 8 and summary.more == 4


def test_ids_refs_and_digests_never_reach_the_default_lines_and_unlisted_fields_are_one_count() -> (
    None
):
    before = {
        "statement": "가",
        "plan_id": "a",
        "hypothesis_refs": ["x"],
        "note_a": "1",
        "note_b": "1",
    }
    after = {
        "statement": "나",
        "plan_id": "b",
        "hypothesis_refs": ["y"],
        "note_a": "2",
        "note_b": "2",
    }
    summary = summarize_change(before, after)
    assert summary is not None
    assert [line.label for line in summary.lines] == ["가설 문장"]
    assert summary.other == 2
    assert {line.label for line in summary.technical} == {
        "plan_id",
        "hypothesis_refs",
        "note_a",
        "note_b",
    }


def _details(*estimates: tuple[str, str, str]) -> dict[str, object]:
    return {
        "effort_estimates": [
            {"dimension": d, "band": b, "estimator_type": who} for d, b, who in estimates
        ],
        "model_id": "m",
    }


def test_a_human_estimate_reads_as_one_korean_line_and_not_as_raw_generation_details() -> None:
    before = {
        "generation_details": _details(("TIME", "MEDIUM", "AI"), ("COST_EFFORT", "HIGH", "AI"))
    }
    after = {
        "generation_details": _details(
            ("TIME", "MEDIUM", "AI"), ("COST_EFFORT", "HIGH", "AI"), ("TIME", "HIGH", "HUMAN")
        )
    }
    summary = summarize_change(before, after)
    assert summary is not None and summary.other == 0 and summary.technical == ()
    (line,) = summary.lines
    assert (line.label, line.before, line.after) == ("시간 추정", "보통(AI)", "김(사람)")
    first = summarize_change(
        {"generation_details": _details()},
        {"generation_details": _details(("COST_EFFORT", "LOW", "AI"))},
    )
    assert first is not None and first.lines[0].label == "비용·노력 추정"
    assert (first.lines[0].before, first.lines[0].after) == ("없음", "낮음(AI)")


def test_a_user_correction_reads_as_the_old_memory_then_the_corrected_one() -> None:
    summary = summarize_user_correction(
        "표본이 작으면 결론을 보류한다", "표본이 작지 않으면 결론을 확정한다"
    )
    (line,) = summary.lines
    assert line.label == "사용자 정정"
    assert (line.before, line.after) == (
        "표본이 작으면 결론을 보류한다",
        "표본이 작지 않으면 결론을 확정한다",
    )
    assert "NEGATION" in summary.flags and summary.text_diff_recommended


def test_a_user_correction_with_a_changed_number_asks_for_the_text_diff() -> None:
    summary = summarize_user_correction("표본 30개", "표본 300개")
    assert summary.flags == ("NUMBER",) and summary.more == 0
