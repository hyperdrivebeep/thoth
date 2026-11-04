"""A cached synchronous tail must not commit READY after its caller deadline."""

import asyncio
from pathlib import Path

import pytest
from tests.embedding_fixtures import PROJECT, fixture, unit_vector

from thoth.domain.embedding_index import EmbeddingJob, EmbeddingVector


@pytest.mark.parametrize("phase", ["checkpoint", "publish"])
async def test_cached_tail_checks_deadline_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    x = fixture(tmp_path)
    try:
        span = x.ingest()
        store = x.stores.embedding_index
        (pending,) = store.jobs(PROJECT)
        now_ms = int(x.service.clock.now().timestamp() * 1000)
        seeded = store.claim(PROJECT, pending.job_id, "synthetic-seed", now_ms)
        store.checkpoint(
            seeded,
            span.text_sha256,
            EmbeddingVector.from_values(unit_vector(span.exact_text)),
            now_ms,
        )
        store.finish(seeded, "FAILED", "SYNTHETIC_SEED")
        store.retry(PROJECT, seeded.job_id, seeded.generation)
        loop = asyncio.get_running_loop()
        observed = [loop.time()]
        deadline = observed[0] + 10
        checkpoint, publish = store.checkpoint, store.publish

        def delayed_checkpoint(
            job: EmbeddingJob, text_sha: str, vector: EmbeddingVector, stamp: int
        ) -> None:
            checkpoint(job, text_sha, vector, stamp)
            observed[0] = deadline + 1

        def delayed_publish(job: EmbeddingJob, stamp: int) -> EmbeddingJob:
            result = publish(job, stamp)
            observed[0] = deadline + 1
            return result

        with monkeypatch.context() as patch:
            patch.setattr(loop, "time", lambda: observed[0])
            if phase == "checkpoint":
                patch.setattr(store, "checkpoint", delayed_checkpoint)
            else:
                patch.setattr(store, "publish", delayed_publish)
            with pytest.raises(TimeoutError):
                await x.service.run_one(PROJECT, seeded.job_id, deadline=deadline)
        (failed,) = store.jobs(PROJECT)
        assert failed.state == "FAILED" and failed.manifest_digest is None
        assert store.ready(PROJECT, span.artifact_id, span.source_version_id) is None
        assert x.worker.calls == []
    finally:
        x.stores.close()
