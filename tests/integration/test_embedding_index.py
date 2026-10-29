import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import delete, update
from tests.embedding_fixtures import PROJECT, STAMP, IndexFixture, fixture, unit_vector

from thoth.adapters.storage import schema
from thoth.adapters.storage.embedding_schema import embedding_jobs, embedding_vectors
from thoth.domain.embedding_index import EmbeddingIndexConfig, EmbeddingIndexError, EmbeddingVector


@pytest.fixture
def index(tmp_path: Path) -> Iterator[IndexFixture]:
    value = fixture(tmp_path)
    try:
        yield value
    finally:
        value.worker.close()
        value.stores.close()


async def test_off_has_no_intent_or_encoding_and_explicit_reconcile_is_idempotent(tmp_path: Path):
    x = fixture(tmp_path, enabled=False)
    try:
        span = x.ingest()
        assert x.stores.embedding_index.jobs(PROJECT) == ()
        assert x.worker.calls == []
        with pytest.raises(EmbeddingIndexError, match="DISABLED"):
            await x.service.drain(PROJECT)
        x.service.configure(PROJECT, True, 0)
        first = x.service.reconcile(PROJECT)
        assert len(first) == 1 and x.service.reconcile(PROJECT) == first
        (ready,) = await x.service.drain(PROJECT)
        assert ready.state == "READY" and ready.manifest_digest
        assert x.service.read_vectors(PROJECT, (span,))[0].data
        assert len(x.worker.calls) == 1
        assert await x.service.drain(PROJECT) == ()
    finally:
        x.stores.close()


async def test_source_transaction_rollback_and_worker_outside_transaction(index: IndexFixture):
    with pytest.raises(RuntimeError, match="injected"), index.stores.immediate_uow.transaction():
        index.ingest("rollback")
        assert len(index.stores.embedding_index.jobs(PROJECT)) == 1
        with pytest.raises(EmbeddingIndexError, match="INSIDE_TRANSACTION"):
            await index.service.drain(PROJECT)
        raise RuntimeError("injected")
    assert index.stores.embedding_index.jobs(PROJECT) == ()
    assert index.stores.artifacts.list_artifacts(PROJECT) == ()
    assert index.worker.calls == []


@pytest.mark.parametrize("point", ["after_vector_stage", "before_ready_cas", "after_ready_cas"])
async def test_checkpoint_publication_fault_retry_and_reopen(index: IndexFixture, point: str):
    span = index.ingest()

    def fail(actual: str) -> None:
        if actual == point:
            raise RuntimeError("injected")

    index.stores.embedding_index.fault = fail
    with pytest.raises(RuntimeError, match="injected"):
        await index.service.drain(PROJECT)
    (failed,) = index.stores.embedding_index.jobs(PROJECT)
    assert failed.state == "FAILED" and failed.manifest_digest is None
    assert (
        index.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
        is None
    )
    assert await index.service.drain(PROJECT) == ()
    cached = index.stores.embedding_index.cached(PROJECT, span.text_sha256)
    assert (cached is None) == (point == "after_vector_stage")
    index.stores.embedding_index.fault = None
    index.service.retry(PROJECT, failed.job_id, failed.generation)
    (ready,) = await index.service.drain(PROJECT)
    assert ready.state == "READY"
    assert len(index.worker.calls) == (2 if point == "after_vector_stage" else 1)


def test_lease_expiry_recovery_old_owner_fenced_and_no_autoretry(index: IndexFixture):
    span = index.ingest()
    store = index.stores.embedding_index
    (pending,) = store.jobs(PROJECT)
    old = store.claim(PROJECT, pending.job_id, "owner:old", 100)
    with pytest.raises(EmbeddingIndexError, match="NOT_PENDING"):
        store.claim(PROJECT, pending.job_id, "owner:second", 101)
    assert store.recover(PROJECT, 60100) == 1
    (failed,) = store.jobs(PROJECT)
    assert failed.state == "FAILED"
    assert store.recover(PROJECT, 60101) == 0
    store.retry(PROJECT, failed.job_id, failed.generation)
    current = store.claim(PROJECT, pending.job_id, "owner:old", 60200)
    vector = EmbeddingVector.from_values(unit_vector(span.exact_text))
    with pytest.raises(EmbeddingIndexError, match="LEASE_LOST"):
        store.checkpoint(old, span.text_sha256, vector, 60201)
    store.finish(old, "FAILED", "old worker")
    store.checkpoint(current, span.text_sha256, vector, 60201)
    assert store.publish(current, 60202).state == "READY"


@pytest.mark.parametrize("change", ["disable", "detach", "revoke", "delete", "replace", "cancel"])
async def test_change_during_encode_cannot_publish(index: IndexFixture, change: str):
    span = index.ingest()
    index.worker.release = asyncio.Event()
    task = asyncio.create_task(index.service.drain(PROJECT))
    await asyncio.wait_for(index.worker.entered.wait(), 2)
    (job,) = index.stores.embedding_index.jobs(PROJECT)
    if change == "disable":
        index.service.configure(PROJECT, False, 1)
    elif change in ("detach", "revoke"):
        index.stores.governance.set_source_binding_state(
            PROJECT,
            "binding:a",
            state="DETACHED" if change == "detach" else "REVOKED",
            updated_at=STAMP.isoformat(),
        )
    elif change == "cancel":
        index.service.cancel(PROJECT, job.job_id)
    else:
        with index.stores.ledger.engine.begin() as conn:
            if change == "delete":
                conn.execute(
                    delete(schema.evidence_spans).where(
                        schema.evidence_spans.c.span_id == span.span_id
                    )
                )
            else:
                conn.execute(
                    update(schema.artifact_versions)
                    .where(schema.artifact_versions.c.source_version_id == span.source_version_id)
                    .values(byte_sha256="0" * 64)
                )
    index.worker.release.set()
    with pytest.raises((EmbeddingIndexError, asyncio.CancelledError)):
        await task
    assert index.stores.embedding_index.jobs(PROJECT)[0].state != "READY"
    with pytest.raises((EmbeddingIndexError, ValueError)):
        index.service.read_vectors(PROJECT, (span,))


async def test_pending_cancel_and_manifest_corruption(index: IndexFixture):
    span = index.ingest()
    (job,) = index.stores.embedding_index.jobs(PROJECT)
    index.service.cancel(PROJECT, job.job_id)
    assert await index.service.drain(PROJECT) == ()
    (cancelled,) = index.stores.embedding_index.jobs(PROJECT)
    index.service.retry(PROJECT, job.job_id, cancelled.generation)
    await index.service.drain(PROJECT)
    with index.stores.ledger.engine.begin() as conn:
        conn.execute(update(embedding_jobs).values(manifest_digest="0" * 64))
    with pytest.raises(EmbeddingIndexError, match="MANIFEST_CORRUPT"):
        index.service.read_vectors(PROJECT, (span,))


async def test_project_cache_isolation_and_blob_corruption(index: IndexFixture):
    span = index.ingest()
    await index.service.drain(PROJECT)
    index.stores.embedding_index.configure(
        EmbeddingIndexConfig(project_id="project:other", enabled=True, actor_ref="local:operator"),
        0,
    )
    assert index.stores.embedding_index.cached("project:other", span.text_sha256) is None
    with index.stores.ledger.engine.begin() as conn:
        conn.execute(update(embedding_vectors).values(vector_sha256="0" * 64))
    with pytest.raises(EmbeddingIndexError, match="CACHE_CORRUPT"):
        index.service.read_vectors(PROJECT, (span,))


async def test_reconcile_invalidates_deleted_source_and_gc_is_project_local(index: IndexFixture):
    span = index.ingest()
    await index.service.drain(PROJECT)
    with index.stores.ledger.engine.begin() as conn:
        conn.execute(
            delete(schema.evidence_spans).where(schema.evidence_spans.c.span_id == span.span_id)
        )
    assert index.stores.embedding_index.reconcile(PROJECT) == 1
    assert index.stores.embedding_index.jobs(PROJECT)[0].state == "STALE"
    assert index.stores.embedding_index.collect_orphans("project:other") == 0
    assert index.stores.embedding_index.collect_orphans(PROJECT) == 1


async def test_source_text_corruption_is_not_a_valid_cached_vector(index: IndexFixture):
    span = index.ingest()
    await index.service.drain(PROJECT)
    with index.stores.ledger.engine.begin() as conn:
        conn.execute(
            update(schema.evidence_spans)
            .where(schema.evidence_spans.c.span_id == span.span_id)
            .values(exact_text="corrupted text")
        )
    current = index.service.artifacts.read_evidence(span.span_id)
    assert current is not None
    with pytest.raises(EmbeddingIndexError, match="SOURCE_CORRUPT"):
        index.service.read_vectors(PROJECT, (current,))
