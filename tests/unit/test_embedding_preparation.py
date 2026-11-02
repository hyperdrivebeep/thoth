import asyncio
import builtins
from pathlib import Path
from typing import Any

import pytest
from tests.embedding_fixtures import process_is_gone, unit_vector

from thoth.adapters.embeddings.onnx_encoder import GraniteOnnxEncoder
from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.domain.embedding_index import EmbeddingIndexError, EmbeddingVector
from thoth.domain.model_asset import EmbeddingAssetDescriptor, EmbeddingLoadBudget


def descriptor(root: str) -> EmbeddingAssetDescriptor:
    return EmbeddingAssetDescriptor(root=root, manifest_digest="0" * 64)


async def test_explicit_prepare_before_query_and_legacy_factory_compatibility() -> None:
    worker = ProcessEmbeddingWorker(
        "tests.embedding_fixtures:DescriptorEncoder", descriptor=descriptor("synthetic")
    )
    try:
        with pytest.raises(EmbeddingIndexError, match="NOT_PREPARED"):
            await worker.encode_one("question", role="QUERY")
        assert worker.last_pid is None
        await worker.prepare()
        assert worker.prepared
        vector = await worker.encode_one("question", role="QUERY")
        assert vector == EmbeddingVector.from_values(unit_vector("question"))
        pid = worker.last_pid
        await worker.prepare()
        assert worker.last_pid == pid
    finally:
        await worker.aclose()
    assert pid is not None and process_is_gone(pid)


@pytest.mark.parametrize(
    "root,reason",
    [
        ("synthetic-bad-hash", "MODEL_HASH_MISMATCH"),
        ("synthetic-load-crash", "EMBEDDING_LOAD_FAILED"),
        ("synthetic-malformed-frame", "EMBEDDING_PROTOCOL_FAILED"),
    ],
)
async def test_startup_errors_are_typed_and_child_is_released(root: str, reason: str) -> None:
    worker = ProcessEmbeddingWorker(
        "tests.embedding_fixtures:DescriptorEncoder", descriptor=descriptor(root)
    )
    try:
        with pytest.raises(EmbeddingIndexError, match=reason):
            await worker.prepare()
        assert not worker.prepared and worker.last_stop_confirmed
        assert worker.last_pid is not None and process_is_gone(worker.last_pid)
    finally:
        await worker.aclose()


async def test_prepare_timeout_does_not_use_query_budget_or_leave_child() -> None:
    value = descriptor("synthetic-slow-load").model_copy(
        update={"load_budget": EmbeddingLoadBudget(timeout_ms=300)}
    )
    worker = ProcessEmbeddingWorker("tests.embedding_fixtures:DescriptorEncoder", descriptor=value)
    try:
        with pytest.raises(EmbeddingIndexError, match="EMBEDDING_TIMEOUT"):
            await worker.prepare()
        assert worker.last_stop_confirmed
        assert worker.last_pid is not None and process_is_gone(worker.last_pid)
    finally:
        await worker.aclose()


def test_optional_dependency_missing_is_explicit_before_asset_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = builtins.__import__

    def absent(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in ("numpy", "onnxruntime", "tokenizers"):
            raise ImportError("synthetic absent dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)
    with pytest.raises(EmbeddingIndexError, match="DEPENDENCY_UNAVAILABLE"):
        GraniteOnnxEncoder(descriptor(str(tmp_path / "not-installed")))


async def test_queued_query_cannot_implicitly_reload_after_previous_child_failure() -> None:
    worker = ProcessEmbeddingWorker(
        "tests.embedding_fixtures:DescriptorEncoder", descriptor=descriptor("synthetic")
    )
    try:
        await worker.prepare()
        pid = worker.last_pid
        first = asyncio.create_task(worker.encode_one("crash", role="QUERY"))
        second = asyncio.create_task(worker.encode_one("queued", role="QUERY"))
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert isinstance(results[0], EmbeddingIndexError)
        assert isinstance(results[1], EmbeddingIndexError)
        assert str(results[1]) == "EMBEDDING_NOT_PREPARED"
        assert worker.last_pid == pid and not worker.prepared
        assert pid is not None and process_is_gone(pid)
    finally:
        await worker.aclose()
