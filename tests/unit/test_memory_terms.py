"""The words of a text: Korean endings do not hide a shared word, and a pair never counts twice."""

from __future__ import annotations

from thoth.domain.memory_terms import matched_words, terms, words


def met(question: str, memory_text: str) -> list[str]:
    return [word.run for word in matched_words(words(question), terms(memory_text))]


def test_a_word_with_a_different_ending_still_meets_the_same_word() -> None:
    assert met("검증이 필요해", "추가 검증이 필요하다") == ["검증이", "필요해"]


def test_a_two_letter_word_stands_alone_and_is_found_whole() -> None:
    assert [w.run for w in words("대조")] == ["대조"] and words("대조")[0].pairs == ()
    assert met("대조 방법", "두 표를 대조 한다") == ["대조"]
    # a two-letter word is not found inside a longer word it only happens to be part of
    assert met("대조", "대조군 설계") == ["대조"]
    assert met("조사", "대조 설계") == []


def test_a_word_with_a_particle_meets_the_bare_word_and_the_other_way_round() -> None:
    assert met("자료를 보자", "자료 정리") == ["자료를"]
    assert met("자료 정리", "자료를 보자") == ["자료"]


def test_one_question_word_is_matched_once_however_many_of_its_pairs_are_found() -> None:
    query = words("분석방법")
    memory = terms("분석방법 분석 석방 방법")
    found = matched_words(query, memory)
    assert [w.run for w in found] == ["분석방법"]
    assert len(query[0].pairs) == 3 and len(found) == 1


def test_short_latin_and_digit_words_are_kept_and_filler_words_are_not() -> None:
    assert [w.run for w in words("AI 와 5G, a is of")] == ["ai", "5g"]
    assert met("AI 규제", "ai regulation") == ["ai"]
    assert words("x") == () and words("") == ()


def test_full_width_and_capital_letters_are_the_same_word() -> None:
    assert met("\uff21\uff29 Safety", "ai safety notes") == ["ai", "safety"]


def test_a_very_long_run_is_handled_without_error_and_stays_small() -> None:
    long_run = "가" * 100_000
    found = words(long_run)
    assert len(found) == 1 and found[0].run == long_run
    assert len(found[0].pairs) <= 1
    assert terms(long_run) >= {long_run}
    # a pair of letters from the very start of the run still works
    assert met("가가가", long_run[:50]) == ["가가가"]
