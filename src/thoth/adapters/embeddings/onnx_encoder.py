"""Pinned CPU batch-1 encoder. All heavy imports and model bytes stay in its child."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from thoth.adapters.embeddings.asset_files import verified_bytes
from thoth.adapters.embeddings.manifest import granite_manifest
from thoth.domain.embedding_index import EmbeddingIndexError
from thoth.domain.model_asset import EmbeddingAssetDescriptor, ModelAssetError
from thoth.domain.retrieval_embedding import DenseModelPin

if TYPE_CHECKING:
    from numpy import float32 as NpFloat32
    from numpy.typing import NDArray


# Passages are cut to their first PASSAGE_TOKEN_CAP tokens before they reach the model. Attention
# memory grows much faster than linearly with length: one 48,985 character page container took
# 33 GB in an unbounded run and exceeds the worker's 512 MiB commit limit long before that.
# Queries are never cut. A passage that fits is encoded exactly as before.
PASSAGE_TOKEN_CAP = 512


def cap_passage_tokens(
    ids: Sequence[int], mask: Sequence[int], types: Sequence[int], cap: int = PASSAGE_TOKEN_CAP
) -> tuple[list[int], list[int], list[int], bool]:
    """Keep the first cap-1 tokens and the closing special token; return whether it was cut."""
    if cap < 2:
        raise ValueError("EMBEDDING_PASSAGE_CAP_INVALID")
    if len(ids) <= cap:
        return list(ids), list(mask), list(types), False
    return (
        [*ids[: cap - 1], ids[-1]],
        [*mask[: cap - 1], mask[-1]],
        [*types[: cap - 1], types[-1]],
        True,
    )


class _SessionOptions(Protocol):
    intra_op_num_threads: int
    inter_op_num_threads: int
    execution_mode: object


class _Input(Protocol):
    name: str


class _Session(Protocol):
    def get_inputs(self) -> Sequence[_Input]: ...
    def run(
        self, output_names: list[str], input_feed: Mapping[str, object]
    ) -> Sequence[object]: ...


class _ExecutionMode(Protocol):
    ORT_SEQUENTIAL: object


class _OrtModule(Protocol):
    """Narrow boundary for ORT's untyped native extension; outputs are validated below."""

    ExecutionMode: _ExecutionMode

    def disable_telemetry_events(self) -> None: ...

    def SessionOptions(self) -> _SessionOptions: ...
    def InferenceSession(
        self,
        model: bytes,
        *,
        sess_options: _SessionOptions,
        providers: list[str],
        enable_fallback: int,
        read_config_from_model: bool,
    ) -> _Session: ...


class GraniteOnnxEncoder:
    # Set by tokens_for on every call; a class default keeps encoders built without __init__
    # (tokenizing tests) working.
    last_source_tokens: int | None = None

    def __init__(self, descriptor: EmbeddingAssetDescriptor) -> None:
        try:
            import numpy
            import onnxruntime as native_ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingIndexError("EMBEDDING_DEPENDENCY_UNAVAILABLE") from exc
        self._np = numpy
        ort = cast(_OrtModule, native_ort)
        ort.disable_telemetry_events()
        self._pin = descriptor.pin
        self.last_truncated = False
        manifest = granite_manifest()
        if descriptor.manifest_digest != manifest.digest or descriptor.pin != DenseModelPin():
            raise EmbeddingIndexError("MODEL_HASH_MISMATCH")
        deadline = time.monotonic() + descriptor.load_budget.timeout_ms / 1000
        specs = {entry.path: entry for entry in manifest.files}
        root = Path(descriptor.root)

        def read(name: str) -> bytes:
            try:
                return verified_bytes(root, specs[name], deadline=deadline)
            except ModelAssetError as exc:
                code = (
                    "MODEL_NOT_INSTALLED"
                    if str(exc) == "MODEL_NOT_INSTALLED"
                    else "MODEL_HASH_MISMATCH"
                )
                raise EmbeddingIndexError(code) from exc

        # Notices and pipeline configs are part of the same pinned installation.
        for name in specs:
            if name not in ("onnx/model_quint8_avx2.onnx", "tokenizer.json"):
                read(name)
        config = json.loads(read("config.json"))
        sentence = json.loads(read("sentence_bert_config.json"))
        pool = json.loads(read("1_Pooling/config.json"))
        if (
            config.get("hidden_size") != 384
            or config.get("max_position_embeddings") != 32768
            or sentence.get("max_seq_length") != 32768
            or pool.get("pooling_mode_cls_token") is not True
            or pool.get("pooling_mode_mean_tokens") is not False
        ):
            raise EmbeddingIndexError("MODEL_HASH_MISMATCH")
        self._max_tokens = manifest.max_tokens
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Parse the tokenizer first, as in the validated batch-1 prototype. Its transient
        # UTF-8/JSON buffers must not overlap the model session's allocations.
        tokenizer_bytes = read("tokenizer.json")
        self._tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
        del tokenizer_bytes
        self._tokenizer.no_padding()
        self._tokenizer.no_truncation()
        model_bytes = read("onnx/model_quint8_avx2.onnx")
        # Exact validated bytes are loaded; ORT never reopens an untrusted model path.
        self._session = ort.InferenceSession(
            model_bytes,
            sess_options=options,
            providers=["CPUExecutionProvider"],
            enable_fallback=0,
            read_config_from_model=False,
        )
        del model_bytes
        self._input_names = tuple(str(item.name) for item in self._session.get_inputs())
        if not {"input_ids", "attention_mask"}.issubset(self._input_names) or not set(
            self._input_names
        ).issubset({"input_ids", "attention_mask", "token_type_ids"}):
            raise EmbeddingIndexError("EMBEDDING_LOAD_FAILED")

    def tokens_for(
        self, text: str, role: Literal["QUERY", "PASSAGE"]
    ) -> tuple[list[int], list[int], list[int], bool]:
        encoded = self._tokenizer.encode(text, add_special_tokens=True)
        ids, mask, types = list(encoded.ids), list(encoded.attention_mask), list(encoded.type_ids)
        self.last_source_tokens = len(cast("list[int]", ids))
        if role == "PASSAGE":
            return cap_passage_tokens(ids, mask, types)
        return ids, mask, types, False

    def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], model: DenseModelPin
    ) -> tuple[float, ...]:
        if model != self._pin:
            raise EmbeddingIndexError("MODEL_HASH_MISMATCH")
        self.last_truncated = False
        ids, mask, types, truncated = self.tokens_for(text, role)
        count = len(ids)
        if count > self._max_tokens:
            raise EmbeddingIndexError("QUERY_TOO_LONG" if role == "QUERY" else "PASSAGE_TOO_LONG")
        np = self._np
        values = {
            "input_ids": np.asarray([ids], dtype=np.int64),
            "attention_mask": np.asarray([mask], dtype=np.int64),
            "token_type_ids": np.asarray([types], dtype=np.int64),
        }
        output = self._session.run(
            ["last_hidden_state"], {name: values[name] for name in self._input_names}
        )
        hidden = cast("NDArray[NpFloat32]", output[0])
        if (
            hidden.shape != (1, count, 384)
            or hidden.dtype != np.float32
            or not np.isfinite(hidden).all()
        ):
            raise EmbeddingIndexError("EMBEDDING_FAILED")
        vectors = hidden[:, 0, :]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norm = float(norms[0, 0])
        if not math.isfinite(norm) or norm <= 0:
            raise EmbeddingIndexError("EMBEDDING_FAILED")
        self.last_truncated = truncated
        return tuple(float(v) for v in (vectors / norms).astype(np.float32)[0])


def create_encoder(descriptor: EmbeddingAssetDescriptor) -> GraniteOnnxEncoder:
    return GraniteOnnxEncoder(descriptor)
