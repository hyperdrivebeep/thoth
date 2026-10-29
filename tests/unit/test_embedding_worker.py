import asyncio
import struct
import time

import pytest
from pydantic import ValidationError
from tests.embedding_fixtures import process_is_gone as _is_gone
from tests.embedding_fixtures import unit_vector

from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.domain.embedding_index import EmbeddingBudget, EmbeddingIndexError, EmbeddingVector

FACTORY = "tests.embedding_fixtures:ChildEncoder"


async def test_child_roundtrip_close_and_no_automatic_start() -> None:
    worker = ProcessEmbeddingWorker(FACTORY)
    assert worker.last_pid is None
    try:
        vector = await worker.encode_one("synthetic", role="PASSAGE")
        assert vector == EmbeddingVector.from_values(unit_vector("synthetic"))
        pid = worker.last_pid
        assert pid is not None and not _is_gone(pid)
    finally:
        await worker.aclose()
    assert worker.last_stop_confirmed and pid is not None and _is_gone(pid)


@pytest.mark.parametrize("text", ["crash", "bad", "network", "memory"])
async def test_child_failure_is_bounded_and_process_is_gone(text: str) -> None:
    worker = ProcessEmbeddingWorker(FACTORY)
    try:
        with pytest.raises(EmbeddingIndexError):
            await worker.encode_one(text, role="PASSAGE")
        assert worker.last_stop_confirmed
        assert worker.last_pid is not None and _is_gone(worker.last_pid)
    finally:
        await worker.aclose()


async def test_deadline_kills_child_and_cancellation_confirms_exit() -> None:
    worker = ProcessEmbeddingWorker(FACTORY, EmbeddingBudget(span_timeout_ms=2000))
    start = time.monotonic()
    with pytest.raises(EmbeddingIndexError, match="TIMEOUT"):
        await worker.encode_one("sleep:30", role="PASSAGE")
    assert time.monotonic() - start < 5
    assert worker.last_pid is not None and _is_gone(worker.last_pid)
    task = asyncio.create_task(worker.encode_one("sleep:30", role="PASSAGE"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.last_stop_confirmed
    assert worker.last_pid is not None and _is_gone(worker.last_pid)
    await worker.aclose()


async def test_queue_bound_priority_and_queued_deadline_does_not_kill_active() -> None:
    worker = ProcessEmbeddingWorker(FACTORY, EmbeddingBudget(query_queue=2, query_timeout_ms=100))
    # Warm startup separately; then a long background span blocks queued queries.
    await worker.encode_one("warm", role="PASSAGE")
    active = asyncio.create_task(worker.encode_one("sleep:0.5", role="PASSAGE"))
    await asyncio.sleep(0.02)
    q1 = asyncio.create_task(worker.encode_one("q1", role="QUERY"))
    q2 = asyncio.create_task(worker.encode_one("q2", role="QUERY"))
    await asyncio.sleep(0)
    with pytest.raises(EmbeddingIndexError, match="QUEUE_FULL"):
        await worker.encode_one("overflow", role="QUERY")
    for task in (q1, q2):
        with pytest.raises(EmbeddingIndexError, match="TIMEOUT"):
            await task
    assert not active.done()
    assert worker.last_pid is not None and not _is_gone(worker.last_pid)
    await active
    await worker.aclose()


@pytest.mark.parametrize(
    "data",
    [b"", struct.pack("<384f", *([0.0] * 384)), struct.pack("<384f", *([float("nan")] * 384))],
)
def test_vector_validation(data: bytes) -> None:
    with pytest.raises(EmbeddingIndexError):
        EmbeddingVector(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("query_timeout_ms", 2001),
        ("workers", 2),
        ("automatic_retries", 1),
        ("commit_limit_bytes", 537000000),
    ],
)
def test_budget_cannot_silently_expand(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        EmbeddingBudget.model_validate({field: value})


async def test_foreground_queries_run_before_waiting_background() -> None:
    worker = ProcessEmbeddingWorker(FACTORY)
    completed: list[str] = []

    async def encode(text: str, role: str) -> None:
        await worker.encode_one(text, role="QUERY" if role == "QUERY" else "PASSAGE")
        completed.append(text)

    try:
        await worker.encode_one("warm", role="PASSAGE")
        active = asyncio.create_task(encode("sleep:0.3", "PASSAGE"))
        await asyncio.sleep(0.02)
        background = asyncio.create_task(encode("background", "PASSAGE"))
        foreground = asyncio.create_task(encode("foreground", "QUERY"))
        await asyncio.gather(active, background, foreground)
        assert completed == ["sleep:0.3", "foreground", "background"]
    finally:
        await worker.aclose()
