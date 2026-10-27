"""G3 1b: BM25 and the two ways of cutting text into terms."""

import math

import pytest

from thoth.domain.lane_scoring import BM25_VERSION, bigram_terms, bm25_scores, identifier_terms


def test_the_score_follows_the_stated_formula() -> None:
    docs = [["x", "y"], ["z"]]
    # N = 2, n = 1, idf = ln(1 + 1.5 / 1.5) = ln 2; average length 1.5, length 2.
    norm = 1 - 0.75 + 0.75 * 2 / 1.5
    expected = math.log(2) * 1 * 2.2 / (1 + 1.2 * norm)
    assert bm25_scores(docs, ["x"])[0] == pytest.approx(expected, abs=1e-9)
    assert bm25_scores(docs, ["x"])[1] == 0.0
    assert "k1=1.2" in BM25_VERSION and "b=0.75" in BM25_VERSION


def test_the_idf_is_never_negative_even_for_a_term_every_document_holds() -> None:
    scores = bm25_scores([["a"], ["a"], ["a"]], ["a"])
    assert all(score > 0 for score in scores)


def test_a_query_term_counts_once_however_often_it_is_written() -> None:
    docs = [["a", "b"], ["c"]]
    assert bm25_scores(docs, ["a", "a", "a"]) == bm25_scores(docs, ["a"])


def test_the_document_count_and_frequencies_are_those_of_the_documents_given() -> None:
    few = bm25_scores([["rare"], ["other"]], ["rare"])[0]
    many = bm25_scores([["rare"], ["other"], ["x"], ["y"], ["z"]], ["rare"])[0]
    assert many > few


def test_a_shorter_document_with_the_same_term_frequency_scores_higher() -> None:
    short, long = bm25_scores([["a"], ["a", "b", "c", "d", "e"]], ["a"])
    assert short > long


def test_equal_documents_score_exactly_alike_and_no_match_scores_zero() -> None:
    scores = bm25_scores([["a", "b"], ["a", "b"], ["c"]], ["a"])
    assert scores[0] == scores[1] > 0 and scores[2] == 0.0
    assert bm25_scores([], ["a"]) == [] and bm25_scores([["a"]], []) == [0.0]


def test_identifier_terms_keep_every_form_of_an_identifier_and_the_pieces_left_over() -> None:
    terms = identifier_terms("fill_nan(None)이 gcc에서 17개가 v1.2와")
    assert {"fill_nan(none)", "fill_nan", "gcc", "17", "개가", "v1.2"} <= set(terms)
    assert "에서" not in terms and "이" not in terms and "와" not in terms


def test_bigram_terms_cut_hangul_into_syllable_pairs_and_keep_symbols_whole() -> None:
    assert bigram_terms("보고서에서 fill_nan v1.2-rc 1") == (
        "보고",
        "고서",
        "서에",
        "fill_nan",
        "v1.2-rc",
    )
    # NFKC and case folding; a function word is not a term.
    assert bigram_terms("\uff27\uff23\uff23 에서") == ("gcc",)
