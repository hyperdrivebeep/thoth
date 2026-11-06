"""Passage token cap: bounded model input, unchanged short inputs, a flag only for cut passages."""

from types import SimpleNamespace
from typing import Literal

import pytest

from thoth.adapters.embeddings.onnx_encoder import (
    PASSAGE_TOKEN_CAP,
    GraniteOnnxEncoder,
    cap_passage_tokens,
)
from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.domain.embedding_index import embedding_key
from thoth.domain.retrieval_embedding import DenseModelPin


def sequence(count: int) -> tuple[list[int], list[int], list[int]]:
    return [0, *range(10, 10 + count - 2), 2], [1] * count, [0] * count


def test_cap_keeps_the_prefix_and_the_closing_token() -> None:
    ids, mask, types = sequence(3000)
    cut_ids, cut_mask, cut_types, truncated = cap_passage_tokens(ids, mask, types, 512)
    assert truncated is True
    assert len(cut_ids) == len(cut_mask) == len(cut_types) == 512
    assert cut_ids[:511] == ids[:511]
    assert cut_ids[-1] == ids[-1] == 2
    assert cut_mask == [1] * 512
    assert cut_types == [0] * 512


@pytest.mark.parametrize("count", [2, 10, 511, 512])
def test_inputs_within_the_cap_are_unchanged(count: int) -> None:
    ids, mask, types = sequence(count)
    assert cap_passage_tokens(ids, mask, types, 512) == (ids, mask, types, False)


def test_one_token_over_the_cap_is_cut() -> None:
    ids, mask, types = sequence(513)
    cut = cap_passage_tokens(ids, mask, types, 512)
    assert cut[3] is True and len(cut[0]) == 512


def test_degenerate_cap_is_rejected() -> None:
    ids, mask, types = sequence(10)
    with pytest.raises(ValueError, match="EMBEDDING_PASSAGE_CAP_INVALID"):
        cap_passage_tokens(ids, mask, types, 1)


def test_default_cap_is_inside_the_measured_memory_envelope() -> None:
    # 512 tokens measured +406 MiB commit in the real worker (limit 512 MiB); 1,024 left 35 MiB.
    assert 2 <= PASSAGE_TOKEN_CAP <= 512
    ids, mask, types = sequence(PASSAGE_TOKEN_CAP * 40)
    assert len(cap_passage_tokens(ids, mask, types)[0]) == PASSAGE_TOKEN_CAP


class FakeTokenizer:
    """Text 'tokens:N' becomes N token ids with CLS first and a closing token last."""

    def encode(self, text: str, add_special_tokens: bool = True) -> SimpleNamespace:
        assert add_special_tokens is True
        ids, mask, types = sequence(int(text.split(":")[1]))
        return SimpleNamespace(ids=ids, attention_mask=mask, type_ids=types)


class TokenizingEncoder(GraniteOnnxEncoder):
    def __init__(self) -> None:  # the real constructor loads a model; only tokenizing is under test
        self._tokenizer = FakeTokenizer()


def encoder() -> GraniteOnnxEncoder:
    return TokenizingEncoder()


@pytest.mark.parametrize(
    ("role", "count", "expected_count", "expected_truncated"),
    [
        # 3000 tokens now fit in eight windows (see test_embedding_window_geometry.py); 5000 do not.
        ("PASSAGE", 5000, PASSAGE_TOKEN_CAP, True),
        ("PASSAGE", 40, 40, False),
        ("PASSAGE", PASSAGE_TOKEN_CAP, PASSAGE_TOKEN_CAP, False),
        ("QUERY", 3000, 3000, False),
    ],
)
def test_encoder_cuts_passages_only(
    role: Literal["QUERY", "PASSAGE"], count: int, expected_count: int, expected_truncated: bool
) -> None:
    ids, mask, types, truncated = encoder().tokens_for(f"tokens:{count}", role)
    original = sequence(count)
    assert len(ids) == len(mask) == len(types) == expected_count
    assert truncated is expected_truncated
    if not expected_truncated:
        assert (ids, mask, types) == original
    else:
        assert ids[:-1] == original[0][: expected_count - 1] and ids[-1] == original[0][-1]


async def test_only_cut_passages_are_counted_by_the_worker() -> None:
    worker = ProcessEmbeddingWorker("tests.embedding_child_encoders:CappedEncoder")
    try:
        assert (await worker.encode_one("short passage", role="PASSAGE")).data
        assert worker.truncated_passages == 0
        assert (await worker.encode_one("long passage", role="PASSAGE")).data
        assert worker.truncated_passages == 1
        assert (await worker.encode_one("long query", role="QUERY")).data
        assert worker.truncated_passages == 1
    finally:
        worker.close()


def test_a_new_preprocessing_version_gets_new_cache_keys() -> None:
    # The cap changes vectors for long passages. Raising the pin's preprocessing version is what
    # keeps those vectors from being mixed with older ones: the content key includes the pin.
    old = DenseModelPin()
    new = old.model_copy(update={"preprocessing": "granite-cls-l2-batch1-question-only:1.1.0"})
    assert embedding_key("a" * 64, old) != embedding_key("a" * 64, new)
    assert embedding_key("a" * 64, old) == embedding_key("a" * 64, DenseModelPin())
