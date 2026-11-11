"""A large project's resident copy is built off the event loop, and the wait for it is kept."""

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from tests.unit.test_dense_material_resident import Index, build, make, scores

pytestmark = pytest.mark.local_retrieval
pytest.importorskip("numpy", reason="numpy 없음(local-retrieval extra)")

SOURCES = 6
DELAY = 0.3  # one blocking read per source, like a SQLite read of a large source


@dataclass
class SlowIndex(Index):
    delay: float = DELAY
    threads: list[int] = field(default_factory=list)

    def read_source_windows(self, project: str, artifact: str, version: str) -> Any:
        self.threads.append(threading.get_ident())
        time.sleep(self.delay)  # blocks whichever thread calls it
        return super().read_source_windows(project, artifact, version)


def slow() -> tuple[SlowIndex, Any]:
    index, spans, _ = build(artifacts=SOURCES, per=10)
    return SlowIndex(index.worker, index.sources), spans


class Ticker:
    """Another coroutine; how late it runs shows how long the loop was blocked."""

    def __init__(self) -> None:
        self.worst = 0.0
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            self.worst = max(self.worst, now - last - 0.01)
            last = now

    async def __aenter__(self) -> "Ticker":
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(0.05)
        self.worst = 0.0
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._task is not None
        self._task.cancel()


async def until_ready(provider: Any, spans: Any) -> Any:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        material = await provider.material("p", "lantern", spans)
        if material is not None and material.unavailable_reason is None:
            return material
        await asyncio.sleep(0.05)
    raise AssertionError("the resident copy was not built in time")


async def test_the_first_question_returns_within_the_wait_and_the_loop_stays_free() -> None:
    index, spans = slow()
    provider = make(index, build_wait=0.4)
    async with Ticker() as ticker:
        started = time.perf_counter()
        first = await provider.material("p", "lantern", spans)
        elapsed = time.perf_counter() - started
        assert first is not None and first.unavailable_reason == "INDEX_PENDING"
        assert elapsed < 0.4 + 0.35  # the wait, not the 1.8 s the reads take
        await asyncio.sleep(1.0)  # the reads go on in the background
        assert ticker.worst < 0.15
    assert threading.get_ident() not in index.threads and index.threads


async def test_after_the_build_the_next_question_uses_dense_with_the_same_scores() -> None:
    index, spans = slow()
    fast, _, _ = build(artifacts=SOURCES, per=10)
    expected = scores(await make(fast).material("p", "lantern", spans))
    provider = make(index, build_wait=0.4)
    assert (await provider.material("p", "lantern", spans)).unavailable_reason == "INDEX_PENDING"  # type: ignore[union-attr]
    got = scores(await until_ready(provider, spans))
    assert got == expected
    reads = index.reads
    assert scores(await provider.material("p", "lantern", spans)) == expected
    assert index.reads == reads  # the copy is reused, not read again


async def test_two_questions_at_once_start_one_build() -> None:
    index, spans = slow()
    provider = make(index, build_wait=10.0)
    first, second = await asyncio.gather(
        provider.material("p", "lantern", spans), provider.material("p", "second", spans)
    )
    assert scores(first) and scores(second)
    assert index.reads == SOURCES


async def test_a_dropped_build_stops_reading_and_leaves_no_copy() -> None:
    index, spans = slow()
    provider = make(index, build_wait=0.2)
    assert (await provider.material("p", "lantern", spans)).unavailable_reason == "INDEX_PENDING"  # type: ignore[union-attr]
    provider.invalidate("p")
    await asyncio.sleep(DELAY * SOURCES + 0.8)  # longer than a whole build
    assert index.reads <= 2  # the read in flight, at most one more
    assert provider.resident_projects() == ()
