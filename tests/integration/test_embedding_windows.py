"""Windowed indexing end to end: keys, storage, resume, orphan cleanup, rebuilds from 1.1.0.

Synthetic encoders and sources only; no downloaded weights and no provider calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import text
from tests.embedding_fixtures import PROJECT, FakeWorker, IndexFixture, fixture, unit_vector
from tests.integration.test_embedding_preprocess_version import INSTALLED_FOLDER_DIGEST

from thoth.adapters.embeddings.manifest import granite_manifest
from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.embedding_index import (
    EmbeddingIndexError,
    EmbeddingIntent,
    EmbeddingVector,
    content_key,
    embedding_key,
    source_refs,
    window_key,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_embedding import (
    GRANITE_ASSET_PREPROCESSING,
    GRANITE_PREPROCESSING_V11,
    MAX_PASSAGE_WINDOWS,
    DenseModelPin,
    window_count,
    window_cut,
)

LONG = 1330  # tokens: four windows, nothing cut
CUT = 5000  # tokens: more than eight windows can hold
V11_PIN = DenseModelPin(preprocessing=GRANITE_PREPROCESSING_V11)


def _keys(x: IndexFixture) -> set[str]:
    with x.stores.ledger.engine.connect() as connection:
        return {r[0] for r in connection.execute(text("SELECT content_key FROM embedding_vectors"))}


def _record(x: IndexFixture, key: str) -> tuple[object, object]:
    with x.stores.ledger.engine.connect() as connection:
        row = connection.execute(
            text("SELECT truncated, source_tokens FROM embedding_vectors WHERE content_key = :k"),
            {"k": key},
        ).one()
    return row[0], row[1]


def _long(x: IndexFixture, name: str = "long", tokens: int = LONG) -> EvidenceSpan:
    span = x.ingest(name)
    x.worker.sizes[span.exact_text] = tokens
    return span


async def test_a_long_passage_is_stored_window_by_window_and_a_short_one_as_before(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path)
    try:
        span, short = _long(x), x.ingest("short")
        (ready, *rest) = [j for j in await x.service.drain(PROJECT)]
        assert ready.state == "READY" and all(j.state == "READY" for j in rest)
        calls = x.worker.window_calls
        assert [w for t, w in calls if t == span.exact_text] == [0, 1, 2, 3]
        assert [w for t, w in calls if t == short.exact_text] == [0]
        pin = DenseModelPin()
        expected = {embedding_key(span.text_sha256, pin), embedding_key(short.text_sha256, pin)} | {
            window_key(span.text_sha256, pin, w) for w in (1, 2, 3)
        }
        assert _keys(x) == expected
        store = x.stores.embedding_index
        windows = store.cached_windows(PROJECT, span.text_sha256)
        assert windows is not None and len(windows) == window_count(LONG) == 4
        assert len({w.sha256 for w in windows}) == 4
        assert store.cached(PROJECT, span.text_sha256, 3) == windows[3]
        assert store.cached(PROJECT, span.text_sha256) == windows[0]
        # The first window carries the size that tells a reader how many windows to expect.
        assert _record(x, embedding_key(span.text_sha256, pin)) == (0, LONG)
        assert store.cached_windows(PROJECT, short.text_sha256) is not None
        assert len(store.cached_windows(PROJECT, short.text_sha256) or ()) == 1
        by_span = x.service.read_window_vectors(PROJECT, (span, short))
        assert [len(w) for w in by_span] == [4, 1]
        assert x.service.read_vectors(PROJECT, (span, short)) == (by_span[0][0], by_span[1][0])
    finally:
        x.worker.close()
        x.stores.close()


async def test_content_past_the_last_window_stays_recorded_as_cut(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x, "huge", CUT)
        assert window_cut(CUT) and window_count(CUT) == MAX_PASSAGE_WINDOWS
        await x.service.drain(PROJECT)
        assert [w for _t, w in x.worker.window_calls] == list(range(MAX_PASSAGE_WINDOWS))
        assert _record(x, embedding_key(span.text_sha256, DenseModelPin())) == (1, CUT)
        windows = x.stores.embedding_index.cached_windows(PROJECT, span.text_sha256)
        assert windows is not None and len(windows) == MAX_PASSAGE_WINDOWS
        assert all(w.truncated is True and w.source_tokens == CUT for w in windows)
    finally:
        x.worker.close()
        x.stores.close()


async def test_an_interrupted_job_resumes_after_the_last_stored_window(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x)
        encoded = 0

        def crash_on_the_third_window(_point: str) -> None:
            nonlocal encoded
            encoded += 1
            if encoded == 3:
                raise RuntimeError("crash before the third window is stored")

        x.service.fault = crash_on_the_third_window
        with pytest.raises(RuntimeError, match="third window"):
            await x.service.drain(PROJECT)
        store = x.stores.embedding_index
        assert store.cached(PROJECT, span.text_sha256, 1) is not None
        assert store.cached(PROJECT, span.text_sha256, 2) is None
        assert store.cached_windows(PROJECT, span.text_sha256) is None  # not complete yet
        (job,) = store.jobs(PROJECT)
        assert job.state == "FAILED"
        x.service.fault = None
        x.service.retry(PROJECT, job.job_id, job.generation)
        assert (await x.service.drain(PROJECT))[0].state == "READY"
        # Windows 0 and 1 were stored before the crash and are not encoded again.
        assert x.worker.window_calls == [
            (span.exact_text, 0),
            (span.exact_text, 1),
            (span.exact_text, 2),
            (span.exact_text, 2),
            (span.exact_text, 3),
        ]
        assert len(store.cached_windows(PROJECT, span.text_sha256) or ()) == 4
    finally:
        x.worker.close()
        x.stores.close()


async def test_a_ready_index_with_a_missing_window_is_incomplete(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x)
        await x.service.drain(PROJECT)
        assert x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
        with x.stores.ledger.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM embedding_vectors WHERE content_key = :k"),
                {"k": window_key(span.text_sha256, DenseModelPin(), 2)},
            )
        assert x.stores.embedding_index.cached_windows(PROJECT, span.text_sha256) is None
        with pytest.raises(EmbeddingIndexError, match="INCOMPLETE"):
            x.service.read_window_vectors(PROJECT, (span,))
    finally:
        x.worker.close()
        x.stores.close()


async def test_a_replaced_later_window_breaks_the_ready_manifest(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x)
        await x.service.drain(PROJECT)
        assert x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
        other = EmbeddingVector.from_values(unit_vector("some other vector"))
        with x.stores.ledger.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE embedding_vectors SET data = :d, vector_sha256 = :s"
                    " WHERE content_key = :k"
                ),
                {
                    "d": other.data,
                    "s": other.sha256,
                    "k": window_key(span.text_sha256, DenseModelPin(), 3),
                },
            )
        with pytest.raises(EmbeddingIndexError, match="EMBEDDING_MANIFEST_CORRUPT"):
            x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
    finally:
        x.worker.close()
        x.stores.close()


def test_publishing_needs_every_window_of_a_long_passage(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x)
        store = x.stores.embedding_index
        (pending,) = store.jobs(PROJECT)
        job = store.claim(PROJECT, pending.job_id, "owner", 10)
        first = EmbeddingVector(
            EmbeddingVector.from_values(unit_vector(span.exact_text)).data,
            truncated=False,
            source_tokens=LONG,
        )
        store.checkpoint(job, span.text_sha256, first, 11)
        with pytest.raises(EmbeddingIndexError, match="INCOMPLETE"):
            store.publish(job, 12)
        for window in (1, 2, 3):
            later = EmbeddingVector.from_values(unit_vector(f"{span.exact_text}#{window}"))
            store.checkpoint(job, span.text_sha256, later, 13, window)
        assert store.publish(job, 14).state == "READY"
    finally:
        x.worker.close()
        x.stores.close()


async def test_the_ready_manifest_of_a_one_window_passage_keeps_its_old_form(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path)
    try:
        span = x.ingest("short")
        (job,) = await x.service.drain(PROJECT)
        vector = x.stores.embedding_index.cached(PROJECT, span.text_sha256)
        assert vector is not None
        old_form = domain_digest(
            "EMBEDDING_READY_MANIFEST",
            "1.0.0",
            canonical_payload(
                {
                    "intent": job.intent,
                    "vectors": [(span.span_id, span.text_sha256, vector.sha256)],
                }
            ),
        )
        assert job.manifest_digest == old_form
    finally:
        x.worker.close()
        x.stores.close()


def _store_ready_1_1_0(x: IndexFixture, span: EvidenceSpan) -> str:
    """A job and a vector exactly as 1.1.0 stored them (one vector per passage)."""
    config = x.stores.embedding_index.configuration(PROJECT)
    intent = EmbeddingIntent(
        project_id=PROJECT,
        artifact_id=span.artifact_id,
        source_version_id=span.source_version_id,
        source_sha256="b" * 64,
        refs=source_refs((span,)),
        model=V11_PIN,
        config_generation=config.generation,
    )
    vector = EmbeddingVector.from_values(unit_vector(span.exact_text))
    stored = config.model_copy(update={"model": V11_PIN}).model_dump_json()
    assert GRANITE_PREPROCESSING_V11 in stored
    with x.stores.ledger.engine.begin() as connection:
        connection.execute(text("DELETE FROM embedding_jobs"))
        connection.execute(
            text("UPDATE embedding_configs SET content_json = :json WHERE project_id = :p"),
            {"json": stored, "p": PROJECT},
        )
        connection.execute(
            text(
                "INSERT INTO embedding_jobs (job_id, project_id, artifact_id, source_version_id,"
                " intent_json, state, generation, manifest_digest)"
                " VALUES (:id, :p, :a, :v, :json, 'READY', 1, :m)"
            ),
            {
                "id": intent.digest,
                "p": PROJECT,
                "a": span.artifact_id,
                "v": span.source_version_id,
                "json": intent.model_dump_json(),
                "m": "c" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO embedding_vectors (project_id, content_key, vector_sha256, data,"
                " truncated, source_tokens) VALUES (:p, :k, :s, :d, 1, 512)"
            ),
            {
                "p": PROJECT,
                "k": embedding_key(span.text_sha256, V11_PIN),
                "s": vector.sha256,
                "d": vector.data,
            },
        )
    return intent.digest


async def test_a_1_1_0_index_goes_stale_and_is_rebuilt_with_windows_without_a_reinstall(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x)
        old_id = _store_ready_1_1_0(x, span)
        states = {j.job_id: j.state for j in x.stores.embedding_index.jobs(PROJECT)}
        assert states == {old_id: "READY"}
        # Read under 1.2.0, an index made the 1.1.0 way is not current.
        assert (
            x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
            is None
        )
        staged = x.service.reconcile(PROJECT)
        states = {j.job_id: j.state for j in x.stores.embedding_index.jobs(PROJECT)}
        assert states[old_id] == "STALE" and len(staged) == 1 and states[staged[0]] == "PENDING"
        (ready,) = await x.service.drain(PROJECT)
        assert ready.state == "READY" and ready.intent.model == DenseModelPin()
        assert [w for _t, w in x.worker.window_calls] == [0, 1, 2, 3]
        assert len(x.stores.embedding_index.cached_windows(PROJECT, span.text_sha256) or ()) == 4
        # The 1.1.0 vector is kept until orphans are collected, and then only it goes.
        old_key = embedding_key(span.text_sha256, V11_PIN)
        assert old_key in _keys(x)
        assert x.stores.embedding_index.collect_orphans(PROJECT) == 1
        assert old_key not in _keys(x) and len(_keys(x)) == 4
        # The model folder is named by the unchanged manifest digest: nothing to download.
        manifest = granite_manifest()
        assert manifest.digest == INSTALLED_FOLDER_DIGEST
        assert manifest.preprocessing == GRANITE_ASSET_PREPROCESSING
    finally:
        x.worker.close()
        x.stores.close()


async def test_collecting_orphans_keeps_every_window_of_a_current_passage(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = _long(x)
        await x.service.drain(PROJECT)
        kept = _keys(x)
        assert len(kept) == 4
        stray = EmbeddingVector.from_values(unit_vector("stray"))
        with x.stores.ledger.engine.begin() as connection:
            for key in (
                window_key("d" * 64, DenseModelPin(), 1),  # a window of a passage no job names
                content_key("e" * 64, DenseModelPin()),
            ):
                connection.execute(
                    text(
                        "INSERT INTO embedding_vectors (project_id, content_key, vector_sha256,"
                        " data) VALUES (:p, :k, :s, :d)"
                    ),
                    {"p": PROJECT, "k": key, "s": stray.sha256, "d": stray.data},
                )
        assert x.stores.embedding_index.collect_orphans(PROJECT) == 2
        assert _keys(x) == kept
        assert len(x.stores.embedding_index.cached_windows(PROJECT, span.text_sha256) or ()) == 4
        assert x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
    finally:
        x.worker.close()
        x.stores.close()


class WindowedEncoder:
    """Child-side stand-in: 'tokens:N' reports N source tokens, each window has its own vector."""

    def __init__(self) -> None:
        self.last_truncated = False
        self.last_source_tokens: int | None = None

    def encode_one(
        self,
        text: str,
        *,
        role: Literal["QUERY", "PASSAGE"],
        model: DenseModelPin,
        window: int = 0,
    ) -> tuple[float, ...]:
        count = int(text.split(":")[1]) if text.startswith("tokens:") else 10
        self.last_source_tokens = count
        self.last_truncated = role == "PASSAGE" and count > 2 and window_cut(count)
        return unit_vector(f"{text}#{window}")


async def test_the_worker_asks_the_encoder_for_one_window_at_a_time() -> None:
    worker = ProcessEmbeddingWorker("tests.integration.test_embedding_windows:WindowedEncoder")
    try:
        first = await worker.encode_one(f"tokens:{LONG}", role="PASSAGE")
        assert first.source_tokens == LONG and first.truncated is None
        assert first.data == EmbeddingVector.from_values(unit_vector(f"tokens:{LONG}#0")).data
        third = await worker.encode_one(f"tokens:{LONG}", role="PASSAGE", window=2)
        assert third.data == EmbeddingVector.from_values(unit_vector(f"tokens:{LONG}#2")).data
        assert third.source_tokens == LONG
        # A passage that fits one window leaves the frame as it was: no size, no flag.
        fits = await worker.encode_one("tokens:100", role="PASSAGE")
        assert fits.source_tokens is None and fits.truncated is None
        assert worker.truncated_passages == 0
        # Only the first window of a cut passage counts it, and every window says so.
        cut = await worker.encode_one(f"tokens:{CUT}", role="PASSAGE")
        again = await worker.encode_one(f"tokens:{CUT}", role="PASSAGE", window=7)
        assert cut.truncated is True and again.truncated is True and again.source_tokens == CUT
        assert worker.truncated_passages == 1
        for bad in (MAX_PASSAGE_WINDOWS, -1):
            with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
                await worker.encode_one("tokens:100", role="PASSAGE", window=bad)
        with pytest.raises(EmbeddingIndexError, match="EMBEDDING_WINDOW_INVALID"):
            await worker.encode_one("question", role="QUERY", window=1)
    finally:
        worker.close()


async def test_fake_worker_without_sizes_still_means_one_window(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = x.ingest("plain")
        assert isinstance(x.worker, FakeWorker)
        await x.service.drain(PROJECT)
        assert x.worker.window_calls == [(span.exact_text, 0)]
        assert _keys(x) == {embedding_key(span.text_sha256, DenseModelPin())}
    finally:
        x.worker.close()
        x.stores.close()
