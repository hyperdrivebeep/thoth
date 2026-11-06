"""Passage windows: geometry, the first window equals the 1.1.0 input, keys and MaxP scoring.

Synthetic tokens and vectors only; no downloaded weights and no provider calls.
"""

import re
from decimal import Decimal
from itertools import pairwise

import pytest
from tests.embedding_fixtures import unit_vector
from tests.unit.test_embedding_token_cap import TokenizingEncoder, sequence

from thoth.adapters.embeddings.onnx_encoder import (
    PASSAGE_TOKEN_CAP,
    cap_passage_tokens,
    window_passage_tokens,
)
from thoth.domain.embedding_index import (
    EmbeddingIndexError,
    EmbeddingVector,
    content_key,
    embedding_key,
    max_window_score,
    window_key,
)
from thoth.domain.retrieval_embedding import (
    GRANITE_PREPROCESSING,
    MAX_PASSAGE_WINDOWS,
    PASSAGE_WINDOW_TOKENS,
    WINDOW_CONTENT_TOKENS,
    WINDOW_OVERLAP_TOKENS,
    DenseModelPin,
    window_bounds,
    window_count,
    window_cut,
)

LAST_COVERED = WINDOW_CONTENT_TOKENS + (MAX_PASSAGE_WINDOWS - 1) * (
    WINDOW_CONTENT_TOKENS - WINDOW_OVERLAP_TOKENS
)


def test_the_version_name_states_the_geometry() -> None:
    match = re.search(r"-win(\d+)o(\d+)m(\d+)-cap(\d+):1\.2\.0$", GRANITE_PREPROCESSING)
    assert match is not None
    assert tuple(int(g) for g in match.groups()) == (
        WINDOW_CONTENT_TOKENS,
        WINDOW_OVERLAP_TOKENS,
        MAX_PASSAGE_WINDOWS,
        PASSAGE_TOKEN_CAP,
    )
    assert PASSAGE_WINDOW_TOKENS == WINDOW_CONTENT_TOKENS + 2


def test_the_approved_geometry() -> None:
    assert (WINDOW_CONTENT_TOKENS, WINDOW_OVERLAP_TOKENS, MAX_PASSAGE_WINDOWS) == (510, 128, 8)


@pytest.mark.parametrize("tokens", [2, 40, 511, 512])
def test_a_passage_that_fits_one_window_has_one(tokens: int) -> None:
    assert window_bounds(tokens) == ((0, tokens - 2),)
    assert window_count(tokens) == 1 and not window_cut(tokens)


def test_one_token_over_needs_a_second_window() -> None:
    assert window_bounds(513) == ((0, 510), (382, 511))
    assert window_count(513) == 2


def test_windows_overlap_by_the_approved_amount_and_cover_the_passage() -> None:
    for tokens in (700, 1330, 2000, 3000):
        bounds = window_bounds(tokens)
        assert bounds[0][0] == 0 and bounds[-1][1] == tokens - 2
        for (_, end), (start, _) in pairwise(bounds):
            assert end - start == WINDOW_OVERLAP_TOKENS
        assert all(end - start <= WINDOW_CONTENT_TOKENS for start, end in bounds)
        assert not window_cut(tokens)


def test_content_past_the_last_window_is_cut_and_recorded_as_cut() -> None:
    assert window_count(LAST_COVERED + 2) == MAX_PASSAGE_WINDOWS and not window_cut(
        LAST_COVERED + 2
    )
    assert window_cut(LAST_COVERED + 3)
    assert window_count(LAST_COVERED + 3) == MAX_PASSAGE_WINDOWS
    assert window_count(10**6) == MAX_PASSAGE_WINDOWS


def test_an_unknown_size_means_one_window() -> None:
    assert window_count(None) == 1


@pytest.mark.parametrize("tokens", [513, 700, 3000, 5000, 40000])
def test_the_first_window_is_exactly_the_input_version_1_1_0_produced(tokens: int) -> None:
    ids, mask, types = sequence(tokens)
    first = window_passage_tokens(ids, mask, types, 0)
    old = cap_passage_tokens(ids, mask, types)
    assert first[:3] == old[:3] and len(first[0]) == PASSAGE_TOKEN_CAP
    # Same ids, same model, so the stored vector is byte-identical to the one 1.1.0 stored.


@pytest.mark.parametrize("tokens", [2, 40, 512])
def test_a_passage_that_fits_is_encoded_whole_as_before(tokens: int) -> None:
    ids, mask, types = sequence(tokens)
    assert window_passage_tokens(ids, mask, types, 0) == (ids, mask, types, False)
    with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
        window_passage_tokens(ids, mask, types, 1)


def test_every_window_is_a_slice_between_the_opening_and_closing_token() -> None:
    ids, mask, types = sequence(1330)
    count = window_count(1330)
    seen: list[int] = []
    for window in range(count):
        w_ids, w_mask, w_types, cut = window_passage_tokens(ids, mask, types, window)
        start, end = window_bounds(1330)[window]
        assert w_ids == [ids[0], *ids[1 + start : 1 + end], ids[-1]]
        assert len(w_ids) <= PASSAGE_TOKEN_CAP and w_mask == [1] * len(w_ids)
        assert w_types == [0] * len(w_ids) and cut is False
        seen += w_ids[1:-1]
    assert set(seen) == set(ids[1:-1])
    with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
        window_passage_tokens(ids, mask, types, count)


def test_a_cut_passage_flags_every_window() -> None:
    ids, mask, types = sequence(LAST_COVERED + 500)
    flags = [window_passage_tokens(ids, mask, types, w)[3] for w in range(MAX_PASSAGE_WINDOWS)]
    assert flags == [True] * MAX_PASSAGE_WINDOWS
    with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
        window_passage_tokens(ids, mask, types, MAX_PASSAGE_WINDOWS)


def test_the_encoder_names_windows_for_passages_only() -> None:
    encoder = TokenizingEncoder()
    ids, _mask, _types, cut = encoder.tokens_for("tokens:1330", "PASSAGE", 2)
    assert ids[1:-1] == sequence(1330)[0][1 + 764 : 1 + 1274] and cut is False
    assert encoder.last_source_tokens == 1330
    with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
        encoder.tokens_for("tokens:1330", "QUERY", 1)
    assert encoder.tokens_for("tokens:1330", "QUERY")[0] == sequence(1330)[0]


def test_window_keys_are_distinct_stable_and_leave_the_first_window_alone() -> None:
    pin, sha = DenseModelPin(), "a" * 64
    keys = [content_key(sha, pin, w) for w in range(MAX_PASSAGE_WINDOWS)]
    assert len(set(keys)) == MAX_PASSAGE_WINDOWS
    assert keys[0] == embedding_key(sha, pin)
    assert keys[3] == window_key(sha, pin, 3) == content_key(sha, pin, 3)
    assert window_key(sha, pin, 1) != window_key("b" * 64, pin, 1)
    older = pin.model_copy(
        update={"preprocessing": "granite-cls-l2-batch1-question-only-cap512:1.1.0"}
    )
    assert content_key(sha, older, 1) != content_key(sha, pin, 1)
    for bad in (0, MAX_PASSAGE_WINDOWS, -1):
        with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
            window_key(sha, pin, bad)


def _vector(name: str) -> EmbeddingVector:
    return EmbeddingVector.from_values(unit_vector(name))


def test_a_passage_scores_as_its_best_window() -> None:
    question = _vector("question")
    unrelated_a, unrelated_b = _vector("zzz-unrelated-1"), _vector("zzz-unrelated-2")
    assert question.data not in (unrelated_a.data, unrelated_b.data)
    assert max_window_score(question, (unrelated_a, unrelated_b)) == Decimal("0.000000000")
    # The answer sits in a later window: the score is that window's, not the first window's.
    assert max_window_score(question, (unrelated_a, unrelated_b, question)) == Decimal(
        "1.000000000"
    )
    assert max_window_score(question, (question, unrelated_a)) == Decimal("1.000000000")
    assert max_window_score(question, (question,)) == max_window_score(
        question, (question, unrelated_a)
    )
    with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOWS_MISSING"):
        max_window_score(question, ())
