"""G3 2: the glossary matcher finds Korean terms, holds back the ambiguous ones, and never guesses.

The glossary here is a small invented one; no real term list is used in any test.
"""

from collections.abc import Sequence

import pytest

from thoth.domain.glossary import (
    MAX_EXPANSIONS,
    Glossary,
    GlossaryConfidence,
    build_glossary,
    entries_that_can_matter,
    entry_id,
    glossary_digest,
    make_entry,
    match_glossary,
)

type Row = tuple[str, str, str, GlossaryConfidence]

ROWS: tuple[Row, ...] = (
    ("정렬 키", "sort key", "db", "HIGH"),
    ("복합 정렬 키", "compound sort key", "db", "MEDIUM"),
    ("정렬", "sort", "frame", "MEDIUM"),
    ("정렬", "alignment", "compiler", "MEDIUM"),
    ("테이블", "table", "db", "HIGH"),
    ("임시 테이블", "temporary table", "db", "MEDIUM"),
    ("키", "key", "db", "HIGH"),
    ("연결", "connection", "db", "HIGH"),
    ("연결", "linking", "compiler", "MEDIUM"),
    ("연결 풀", "connection pool", "db", "HIGH"),
    ("스키마", "schema", "db", "HIGH"),
    ("스키마", "schema", "frame", "MEDIUM"),
    ("중앙값", "median", "perf", "HIGH"),
    ("CUDA 커널", "CUDA kernel", "ml", "HIGH"),
    ("cuda", "cuda", "ml", "MEDIUM"),
)


def glossary(rows: Sequence[Row] = ROWS) -> Glossary:
    return build_glossary([make_entry(*row) for row in rows])


def found(text: str, floor: GlossaryConfidence = "MEDIUM") -> list[tuple[str, str]]:
    return [(f.matched, f.en) for f in match_glossary([text], glossary(), floor).fired]


def held(text: str, floor: GlossaryConfidence = "MEDIUM") -> list[str]:
    return [a.matched for a in match_glossary([text], glossary(), floor).abstained]


def test_the_longest_phrase_at_a_place_wins_and_the_shorter_ones_inside_it_do_not_fire() -> None:
    assert found("복합 정렬 키가 뭐야") == [("복합 정렬 키", "compound sort key")]
    assert found("임시 테이블을 만들어") == [("임시 테이블", "temporary table")]
    # The same words in another order are different phrases, found one by one.
    assert found("테이블의 정렬 키") == [("테이블", "table"), ("정렬 키", "sort key")]


def test_spaces_inside_a_term_are_optional() -> None:
    assert found("정렬 키가 뭐야") == [("정렬 키", "sort key")]
    assert found("정렬키가 뭐야") == [("정렬키", "sort key")]
    assert found("정렬   키는") == [("정렬   키", "sort key")]
    assert found("임시테이블은") == [("임시테이블", "temporary table")]


def test_the_right_edge_ends_the_word_or_is_a_listed_particle() -> None:
    for tail in (
        "",
        "이",
        "가",
        "은",
        "는",
        "을",
        "를",
        "의",
        "에서",
        "에서는",
        "으로",
        "으로는",
        "도",
    ):
        assert found("테이블" + tail) == [("테이블", "table")], tail
    assert found("테이블, 만들기") == [("테이블", "table")]
    # A longer word that only starts with the term is another word.
    assert found("테이블스페이스") == [] and found("테이블링") == []
    # "정렬 키보드" does not end the term "정렬 키", and "정렬" alone is ambiguous.
    assert found("정렬 키보드") == [] and held("정렬 키보드") == ["정렬"]


def test_the_left_edge_must_not_continue_a_word() -> None:
    assert found("대테이블") == [] and found("가상테이블") == []
    assert found("(테이블)") == [("테이블", "table")]


def test_typed_identifiers_are_masked_before_anything_is_expanded() -> None:
    assert found("src/cuda/kernel.py 파일의 cuda 설정") == [("cuda", "cuda")]
    assert found("config.yaml에서 테이블을") == [("테이블", "table")]
    assert found("v1.2.3으로 테이블") == [("테이블", "table")]
    assert found("--force가 테이블") == [("테이블", "table")]
    # A term that is only part of an identifier is not expanded; the same word on its own is.
    for masked in ("src/cuda/kernel.py를 봐", "cuda==12.1 설치", "--cuda 옵션", "CUDA_HOME 환경"):
        assert found(masked) == [], masked
    assert found("cuda 설정") == [("cuda", "cuda")]


def test_a_korean_term_with_two_translations_is_not_expanded() -> None:
    assert found("연결을 설정") == [] and held("연결을 설정") == ["연결"]
    assert found("정렬을 바꿔") == [] and held("정렬을 바꿔") == ["정렬"]
    # A longer term that is not ambiguous at the same place is found instead.
    assert found("연결 풀을 설정") == [("연결 풀", "connection pool")]
    # The domain of an entry plays no part: the same translation in two domains is one concept.
    matches = match_glossary(["스키마가 뭐야"], glossary(), "MEDIUM")
    assert matches.abstained == () and matches.expansions == ("schema",)
    (term,) = matches.fired
    assert len(term.entry_ids) == 2


def test_a_term_of_one_syllable_is_never_matched_alone() -> None:
    assert found("키가 필요해") == [] and held("키가 필요해") == []
    assert found("정렬 키") == [("정렬 키", "sort key")]


def test_the_confidence_floor_chooses_entries_but_ambiguity_is_judged_on_all_of_them() -> None:
    # Under a HIGH floor the MEDIUM phrase is not an entry, so its shorter HIGH part is found.
    assert found("임시 테이블을 만들어", floor="HIGH") == [("테이블", "table")]
    assert found("정렬 키가 뭐야", floor="HIGH") == [("정렬 키", "sort key")]
    # 연결 is HIGH for one translation and MEDIUM for another: a HIGH floor does not make it clear.
    assert found("연결을 설정", floor="HIGH") == [] and held("연결을 설정", floor="HIGH") == [
        "연결"
    ]


def test_only_korean_is_expanded_and_an_expansion_is_not_expanded_again() -> None:
    assert found("sort key and table") == []
    chain = glossary([("가나다", "라마바", "d", "HIGH"), ("라마바", "사아자", "d", "HIGH")])
    matches = match_glossary(["가나다에서"], chain, "MEDIUM")
    assert matches.expansions == ("라마바",)
    # The English side is never read as a Korean key.
    reverse = glossary([("테이블", "table", "d", "HIGH")])
    assert match_glossary(["table"], reverse, "MEDIUM").expansions == ()


def test_at_most_ten_terms_are_used_and_the_rest_are_counted() -> None:
    rows: list[Row] = [
        (f"용어{chr(0xAC00 + i)}{chr(0xAC00 + i)}", f"term{i}", "d", "HIGH") for i in range(12)
    ]
    text = " ".join(row[0] for row in rows)
    matches = match_glossary([text], glossary(rows), "MEDIUM")
    assert len(matches.expansions) == MAX_EXPANSIONS == 10
    assert matches.expansions == tuple(f"term{i}" for i in range(10)) and matches.dropped == 2


def test_the_question_and_each_query_are_read_and_a_term_is_used_once() -> None:
    matches = match_glossary(["중앙값이 뭐야", "테이블 중앙값"], glossary(), "MEDIUM")
    assert matches.expansions == ("median", "table")


def test_the_same_texts_always_give_the_same_matches() -> None:
    texts = ["테이블의 정렬 키와 연결을 중앙값으로"]
    first = match_glossary(texts, glossary(), "MEDIUM")
    assert first == match_glossary(texts, glossary(list(reversed(ROWS))), "MEDIUM")


def test_an_entry_is_known_by_its_content_and_the_digest_by_all_entries_in_any_order() -> None:
    entry = make_entry("테이블", "table", "db", "HIGH")
    assert entry.entry_id == entry_id("테이블", "table", "db") and len(entry.entry_id) == 16
    assert entry_id("테이블", "table", "db2") != entry.entry_id
    base = [make_entry(*row) for row in ROWS]
    assert glossary_digest(base) == glossary_digest(list(reversed(base)))
    assert len(glossary_digest(base)) == 64
    changed = [make_entry("테이블", "table", "db", "MEDIUM"), *base[1:]]
    assert glossary_digest(changed) != glossary_digest(base)
    assert glossary_digest(base[:-1]) != glossary_digest(base)


SAMPLES = (
    "테이블의 정렬 키가 뭐야",
    "정렬키와 정렬 키보드",
    "복합정렬키는 무엇",
    "연결 풀과 연결을 비교해줘",
    "src/cuda/kernel.py 파일의 cuda 설정",
    "임시테이블에서 중앙값을 구해",
    "스키마와 키 그리고 정렬",
    "config.yaml에서 테이블스페이스를 대테이블",
    "CUDA   커널의 연결",
    "table sort key",
)


@pytest.mark.parametrize("floor", ["HIGH", "MEDIUM"])
def test_the_entries_that_can_matter_are_enough_to_reproduce_every_match(
    floor: GlossaryConfidence,
) -> None:
    whole = glossary()
    for text in SAMPLES:
        subset = Glossary(entries_that_can_matter([text], whole), whole.digest, verified=False)
        assert len(subset.entries) <= len(whole.entries)
        assert match_glossary([text], subset, floor) == match_glossary([text], whole, floor), text
    both = Glossary(entries_that_can_matter(SAMPLES, whole), whole.digest, verified=False)
    assert match_glossary(SAMPLES, both, floor) == match_glossary(SAMPLES, whole, floor)


def test_a_text_with_no_korean_term_needs_no_entries() -> None:
    assert entries_that_can_matter(["what is a sort key"], glossary()) == ()
    assert [e.ko for e in entries_that_can_matter(["정렬키"], glossary())] == [
        "정렬 키",
        "정렬",
        "정렬",
    ]
