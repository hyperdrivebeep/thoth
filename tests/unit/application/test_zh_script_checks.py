"""A Chinese answer is not held to the Hangul-question script checks; ko and legacy are."""

from typing import cast

from tests.unit.application.test_hypothesis_table_filler import (
    KOREAN,
    dropped,
    fill_of,
    merged,
    portfolio,
    row,
    table_of,
)
from tests.unit.services.test_research_followup_contract import request_ref

from thoth.application.services.request_language import script_check_problem, validation_problem
from thoth.application.services.text_script import language_problem
from thoth.domain.research_execution import ResearchBoundary, ResearchWork, research_work
from thoth.domain.response_language import ResponseLanguage

CHINESE = "检测率更差"


def work_in(language: ResponseLanguage) -> ResearchWork:
    return ResearchWork(
        request_ref(), KOREAN, cast(ResearchBoundary, object()), response_language=language
    )


def test_the_script_check_basis_per_language():
    assert script_check_problem(None, KOREAN) == KOREAN
    assert script_check_problem("en", KOREAN) == KOREAN
    assert script_check_problem("ko", KOREAN).startswith("한국어 ")
    assert script_check_problem("zh-Hans", KOREAN) == ""
    # A Korean basis demands Hangul; a Chinese one demands nothing.
    assert language_problem(CHINESE, script_check_problem("ko", KOREAN)) == "LANGUAGE"
    assert language_problem(CHINESE, script_check_problem("zh-Hans", KOREAN)) is None


def fill_chinese_cell():
    return merged(portfolio({}), fill_of("h1", row("h1", "h2", CHINESE)))


def test_a_chinese_cell_for_a_korean_question_is_dropped_without_a_language_or_for_korean():
    result, changed = fill_chinese_cell()  # no explicit language: the legacy Hangul question rule
    assert changed == 0 and table_of(result, "h1").get("h2") is None
    assert dropped(result, "h1") == [("t-h1", "h2", "EXPECTED_LANGUAGE_MISMATCH")]
    token = research_work.set(work_in("ko"))
    try:
        result, changed = fill_chinese_cell()
    finally:
        research_work.reset(token)
    assert changed == 0 and dropped(result, "h1") == [("t-h1", "h2", "EXPECTED_LANGUAGE_MISMATCH")]


def test_the_same_cell_is_kept_for_a_chinese_answer_to_a_korean_question():
    token = research_work.set(work_in("zh-Hans"))
    try:
        assert validation_problem(KOREAN) == ""
        result, changed = fill_chinese_cell()
    finally:
        research_work.reset(token)
    assert changed == 1 and table_of(result, "h1")["h2"] == CHINESE
    assert dropped(result, "h1") == []


JAPANESE = "検出率が悪い"


def test_a_japanese_cell_is_kept_for_a_japanese_answer_to_a_korean_question_and_dropped_for_ko():
    assert script_check_problem("ja", KOREAN) == ""
    assert language_problem(JAPANESE, script_check_problem("ja", KOREAN)) is None
    assert language_problem(JAPANESE, script_check_problem("ko", KOREAN)) == "LANGUAGE"
    token = research_work.set(work_in("ja"))
    try:
        assert validation_problem(KOREAN) == ""
        result, changed = merged(portfolio({}), fill_of("h1", row("h1", "h2", JAPANESE)))
    finally:
        research_work.reset(token)
    assert changed == 1 and table_of(result, "h1")["h2"] == JAPANESE
    assert dropped(result, "h1") == []
