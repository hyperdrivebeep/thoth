import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import insert
from tests.embedding_fixtures import PROJECT, STAMP, FakeWorker, fixture, unit_vector

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import schema
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.resource_scope_composition import create_resources
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.embedding_index import EmbeddingIndexError, EmbeddingVector
from thoth.domain.resource_scope import (
    ResourceScopeBody,
    ResourceScopeError,
    ResourceScopeReceiptBody,
    seal_resource_scope,
    seal_resource_scope_receipt,
)


async def test_reopen_partial_checkpoint_requires_manual_retry_and_reuses_blob(tmp_path: Path):
    x = fixture(tmp_path)
    span = x.ingest()
    store = x.stores.embedding_index
    (pending,) = store.jobs(PROJECT)
    old = store.claim(PROJECT, pending.job_id, "dead-owner", 1)
    store.checkpoint(
        old, span.text_sha256, EmbeddingVector.from_values(unit_vector(span.exact_text)), 2
    )
    x.stores.close()
    stores = SqliteStoreFactory().open(tmp_path)
    try:
        clock, ids = SystemClock(), UuidIdGenerator()
        artifacts = create_resources(
            stores.ledger, stores, stores.projects, stores.governance, clock, ids
        )
        service = EmbeddingIndexService(
            store=stores.embedding_index,
            artifacts=artifacts,
            governance=stores.governance,
            projects=stores.projects,
            worker=x.worker,
            uow=stores.immediate_uow,
            clock=clock,
            ids=ids,
        )
        assert service.recover(PROJECT) == 1
        assert await service.drain(PROJECT) == ()
        (failed,) = stores.embedding_index.jobs(PROJECT)
        service.retry(PROJECT, failed.job_id, failed.generation)
        assert (await service.drain(PROJECT))[0].state == "READY"
        assert x.worker.calls == []
        assert len(service.read_vectors(PROJECT, (span,))) == 1
    finally:
        stores.close()


def test_two_claimants_have_one_winner(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        x.ingest()
        (job,) = x.stores.embedding_index.jobs(PROJECT)

        def claim(owner: str) -> str:
            try:
                x.stores.embedding_index.claim(PROJECT, job.job_id, owner, 100)
                return "claimed"
            except EmbeddingIndexError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("first", "second")))
        assert sorted(results) == ["EMBEDDING_JOB_NOT_PENDING", "claimed"]
    finally:
        x.stores.close()


async def test_new_version_same_timestamp_rejects_old_ready_and_reconcile_reconnects(
    tmp_path: Path,
):
    x = fixture(tmp_path)
    try:
        span = x.ingest()
        await x.service.drain(PROJECT)
        with x.stores.ledger.engine.begin() as conn:
            conn.execute(
                insert(schema.artifact_versions).values(
                    source_version_id="source:later",
                    artifact_id=span.artifact_id,
                    version_label="later",
                    structure_metadata_json="{}",
                    byte_sha256=span.text_sha256,
                    retrieved_at=STAMP.isoformat(),
                )
            )
        with pytest.raises(EmbeddingIndexError, match="INDEX_INCOMPLETE"):
            x.service.read_vectors(PROJECT, (span,))
        assert x.stores.embedding_index.reconcile(PROJECT) == 1
        assert x.stores.embedding_index.jobs(PROJECT)[0].state == "STALE"
    finally:
        x.stores.close()


async def test_detach_reattach_explicit_reconcile_reuses_cached_vector(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        x.ingest()
        await x.service.drain(PROJECT)
        x.stores.governance.set_source_binding_state(
            PROJECT, "binding:a", state="DETACHED", updated_at=STAMP.isoformat()
        )
        x.stores.governance.set_source_binding_state(
            PROJECT, "binding:a", state="ACTIVE", updated_at=STAMP.isoformat()
        )
        assert await x.service.drain(PROJECT) == ()
        x.service.reconcile(PROJECT)
        assert (await x.service.drain(PROJECT))[0].state == "READY"
        assert len(x.worker.calls) == 1
    finally:
        x.stores.close()


async def test_independent_span_scope_change_during_encode_refuses_publication(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        span = x.ingest()
        artifacts = x.service.artifacts
        assert isinstance(artifacts, ScopedArtifactLedger)
        old = artifacts.scopes.ensure_derived(
            PROJECT, span.span_id, (span.artifact_id,), is_new=True
        )
        x.worker.release = asyncio.Event()
        task = asyncio.create_task(x.service.drain(PROJECT))
        await asyncio.wait_for(x.worker.entered.wait(), 2)
        body = ResourceScopeBody.model_validate(old.model_dump(exclude={"record_digest"}))
        changed = seal_resource_scope(
            body.model_copy(
                update={
                    "revision": old.revision + 1,
                    "previous_digest": old.record_digest,
                    "parent_refs": ("missing-parent",),
                    "receipt_ref": "receipt:scope-change",
                }
            )
        )
        receipt = seal_resource_scope_receipt(
            ResourceScopeReceiptBody(
                receipt_id=changed.receipt_ref,
                project_id=PROJECT,
                resource_ref=span.span_id,
                before_digest=old.record_digest,
                after_digest=changed.record_digest,
                event_kind=changed.event_kind,
                actor_ref=changed.actor_ref,
                session_ref=None,
                created_at=STAMP,
            )
        )
        x.stores.resource_scopes.append(changed, receipt, expected_revision=old.revision)
        x.worker.release.set()
        with pytest.raises(ResourceScopeError):
            await task
        assert x.stores.embedding_index.jobs(PROJECT)[0].state == "FAILED"
        with pytest.raises(ResourceScopeError):
            x.service.read_vectors(PROJECT, (span,))
    finally:
        x.stores.close()


async def test_encode_before_checkpoint_crash_has_no_blob_and_explicit_retry(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        span = x.ingest()

        def fail(_point: str) -> None:
            raise RuntimeError("crash before checkpoint")

        x.service.fault = fail
        with pytest.raises(RuntimeError, match="before checkpoint"):
            await x.service.drain(PROJECT)
        assert x.stores.embedding_index.cached(PROJECT, span.text_sha256) is None
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        assert job.state == "FAILED"
        assert await x.service.drain(PROJECT) == ()
        x.service.fault = None
        x.service.retry(PROJECT, job.job_id, job.generation)
        assert (await x.service.drain(PROJECT))[0].state == "READY"
        assert len(x.worker.calls) == 2
    finally:
        x.stores.close()


def test_missing_checkpoint_cannot_be_published(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        x.ingest()
        (pending,) = x.stores.embedding_index.jobs(PROJECT)
        job = x.stores.embedding_index.claim(PROJECT, pending.job_id, "owner", 10)
        with pytest.raises(EmbeddingIndexError, match="INDEX_INCOMPLETE"):
            x.stores.embedding_index.publish(job, 11)
        assert x.stores.embedding_index.jobs(PROJECT)[0].state == "RUNNING"
    finally:
        x.stores.close()


async def test_whole_job_timeout_cancels_inflight_encoding_without_auto_retry(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        x.worker.budget = x.worker.budget.model_copy(update={"job_timeout_ms": 100})
        x.service.configure(PROJECT, True, 1)
        x.ingest()
        x.worker.release = asyncio.Event()
        with pytest.raises(TimeoutError):
            await x.service.drain(PROJECT)
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        assert job.state == "FAILED" and job.reason == "TimeoutError"
        assert await x.service.drain(PROJECT) == ()
        assert len(x.worker.calls) == 1
    finally:
        x.stores.close()


async def test_read_only_actor_cannot_execute_index_work(tmp_path: Path):
    x = fixture(tmp_path)
    try:
        x.ingest()
        actor = AuthenticatedActorContext(
            actor_id="local:operator",
            session_id="session:synthetic",
            project_id=PROJECT,
            role_assignment_id="role:synthetic",
            role="READER",
            capabilities=("READ",),
            data_scopes=("PROJECT",),
        )
        with (
            authenticated_actor_scope(actor),
            pytest.raises(EmbeddingIndexError, match="SCOPE_DENIED"),
        ):
            await x.service.drain(PROJECT)
        assert x.worker.calls == []
        assert x.stores.embedding_index.jobs(PROJECT)[0].state == "PENDING"
    finally:
        x.stores.close()


async def test_project_drain_has_one_cumulative_job_budget(tmp_path: Path):
    class SlowWorker(FakeWorker):
        async def encode_one(
            self, text: str, *, role: Literal["QUERY", "PASSAGE"]
        ) -> EmbeddingVector:
            await asyncio.sleep(0.6)
            return await super().encode_one(text, role=role)

    x = fixture(tmp_path)
    try:
        worker = SlowWorker()
        worker.budget = worker.budget.model_copy(update={"job_timeout_ms": 1000})
        x.service.worker = worker
        x.service.configure(PROJECT, True, 1)
        x.ingest("a")
        x.ingest("b")
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await x.service.drain(PROJECT)
        assert asyncio.get_running_loop().time() - started < 1.8
        assert sorted(j.state for j in x.stores.embedding_index.jobs(PROJECT)) == [
            "FAILED",
            "READY",
        ]
    finally:
        x.stores.close()
