"""Local retrieval is on for every project; its index is built in the background."""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, insert, select
from tests.embedding_fixtures import PROJECT, IndexFixture, fixture, sample_assets

from thoth.adapters.embeddings.download import FixedModelDownload
from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.adapters.storage.embedding_schema import embedding_jobs, embedding_vectors
from thoth.application.services.dense_opt_in import ProjectDenseOptIn
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.local_retrieval import LocalRetrievalService
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.embedding_index import EmbeddingIndexConfig

READ_SOURCE = "_read_source"  # the service's read of a job's spans; patched to make it slow


async def setup(
    tmp_path: Path, *, enabled: bool = True
) -> tuple[IndexFixture, LocalRetrievalService]:
    source, assets = sample_assets(tmp_path)
    await assets.import_directory(source)
    x = fixture(tmp_path / "workspace", enabled=enabled)
    service = LocalRetrievalService(
        mode=DeploymentMode.LOCAL,
        assets=assets,
        downloader=FixedModelDownload(assets),
        store=x.stores.embedding_index,
        projects=x.stores.projects,
        artifacts=x.service.artifacts,
        sessions=x.stores.auth,
        governance=x.stores.governance,
        clock=SystemClock(),
        dependency_available=lambda: True,
        factory=lambda _: x.service,
    )
    return x, service


def states(x: IndexFixture) -> list[str]:
    return sorted(job.state for job in x.stores.embedding_index.jobs(PROJECT))


# --- the default ---------------------------------------------------------------------------------


def test_a_project_nobody_configured_is_on_and_a_stored_off_stays_off_after_a_reopen(
    tmp_path: Path,
) -> None:
    stores = SqliteStoreFactory().open(tmp_path)
    try:
        never = stores.embedding_index.configuration("project:never-configured")
        assert (never.enabled, never.generation) == (True, 1)
        stores.embedding_index.configure(
            EmbeddingIndexConfig(
                project_id="project:chosen-off", enabled=False, actor_ref="local:operator"
            ),
            1,
        )
    finally:
        stores.close()
    reopened = SqliteStoreFactory().open(tmp_path)
    try:
        off = reopened.embedding_index.configuration("project:chosen-off")
        assert (off.enabled, off.generation) == (False, 2)
        assert reopened.embedding_index.configuration("project:never-configured").enabled
    finally:
        reopened.close()


# --- background indexing ------------------------------------------------------------------------


async def test_a_new_project_with_sources_is_indexed_without_a_click_and_scores_with_dense(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy", reason="numpy 없음(local-retrieval extra)")
    from thoth.application.services.dense_material import DenseMaterialProvider
    from thoth.domain.behavior_policy import RetrievalRoutingPolicy
    from thoth.domain.retrieval_lane import RetrievalRequest, select_retrieval_context

    x, service = await setup(tmp_path)
    try:
        first, second = x.ingest("a"), x.ingest("b")
        assert states(x) == ["PENDING", "PENDING"] and x.worker.calls == []
        assert x.stores.embedding_index.needs_indexing(PROJECT)
        await service.auto.run_pass()
        assert states(x) == ["READY", "READY"] and len(x.worker.calls) == 2
        assert not x.stores.embedding_index.needs_indexing(PROJECT)
        applies = ProjectDenseOptIn(store=x.stores.embedding_index, governance=x.stores.governance)
        assert applies(PROJECT)
        provider = DenseMaterialProvider(
            stamp=x.stores.embedding_index.index_stamp, index=lambda: x.service
        )
        evidence = (first, second)
        material = await provider.material(PROJECT, "synthetic question", evidence)
        assert material is not None and material.unavailable_reason is None
        selection = select_retrieval_context(
            RetrievalRequest("synthetic question", (), evidence, (), None, dense=material),
            RetrievalRoutingPolicy(max_spans=12, exposure="PROJECT_OPT_IN"),
        )
        assert selection.routing is not None and selection.routing.dense_used is True
    finally:
        service.close()
        x.stores.close()


async def test_a_project_a_person_switched_off_is_never_touched(tmp_path: Path) -> None:
    x, service = await setup(tmp_path, enabled=False)
    try:
        x.ingest("a")
        assert x.stores.embedding_index.jobs(PROJECT) == ()
        assert not x.stores.embedding_index.needs_indexing(PROJECT)
        await service.auto.run_pass()
        assert x.worker.calls == [] and x.stores.embedding_index.jobs(PROJECT) == ()
        applies = ProjectDenseOptIn(store=x.stores.embedding_index, governance=x.stores.governance)
        assert not applies(PROJECT)
    finally:
        service.close()
        x.stores.close()


async def test_even_when_asked_directly_a_switched_off_project_is_not_indexed(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path, enabled=False)
    try:
        x.ingest("a")
        await service.auto.run_project(PROJECT)
        assert x.worker.calls == [] and x.stores.embedding_index.jobs(PROJECT) == ()
    finally:
        service.close()
        x.stores.close()


async def test_a_slow_reconcile_does_not_use_up_the_time_budget_of_the_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.worker.budget = x.worker.budget.model_copy(update={"job_timeout_ms": 10_000})
        x.service.configure(PROJECT, True, 1)
        service.bind_index(x.service)  # no model files to read: only the reconcile is slow
        x.ingest("a")
        loop = asyncio.get_running_loop()
        reconciled_at: list[float] = []
        deadlines: list[float] = []
        original = x.service.areconcile
        drain_project = service.drain_project

        async def slow(project: str) -> tuple[str, ...]:
            await asyncio.sleep(0.3)
            ids = await original(project)
            reconciled_at.append(loop.time())
            return ids

        async def spy(project: str, deadline: float, **kwargs: Any) -> None:
            deadlines.append(deadline)
            await drain_project(project, deadline, **kwargs)

        monkeypatch.setattr(x.service, "areconcile", slow)
        monkeypatch.setattr(service, "drain_project", spy)
        await service.auto.run_project(PROJECT)
        # The whole budget (10 s) is left for the jobs once the reconcile is done.
        assert deadlines[0] >= reconciled_at[0] + 10.0
        assert states(x) == ["READY"]
    finally:
        service.close()
        x.stores.close()


async def test_drain_counts_its_time_budget_from_after_the_jobs_are_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = fixture(tmp_path / "workspace")
    try:
        x.worker.budget = x.worker.budget.model_copy(update={"job_timeout_ms": 10_000})
        x.service.configure(PROJECT, True, 1)
        x.ingest("a")
        loop = asyncio.get_running_loop()
        reconciled_at: list[float] = []
        deadlines: list[float] = []
        original = x.stores.embedding_index.reconcile
        run_one = x.service.run_one

        def slow(project: str) -> Any:
            time.sleep(0.3)  # queueing a large project, not running a job
            ids = original(project)
            reconciled_at.append(loop.time())
            return ids

        async def spy(
            project: str, job_id: str, *, deadline: float | None = None, **kwargs: Any
        ) -> Any:
            assert deadline is not None
            deadlines.append(deadline)
            return await run_one(project, job_id, deadline=deadline, **kwargs)

        monkeypatch.setattr(x.stores.embedding_index, "reconcile", slow)
        monkeypatch.setattr(x.service, "run_one", spy)
        await x.service.drain(PROJECT)
        assert deadlines[0] >= reconciled_at[0] + 10.0
        assert states(x) == ["READY"]
    finally:
        x.stores.close()


async def test_without_the_runtime_component_nothing_runs_and_nothing_applies(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        service.dependency_available = lambda: False
        await service.auto.run_pass()
        assert x.worker.calls == [] and states(x) == ["PENDING"]
        service.dependency_available = lambda: True
        await service.auto.run_pass()
        assert states(x) == ["READY"]
        absent = ProjectDenseOptIn(
            store=x.stores.embedding_index,
            governance=x.stores.governance,
            dependencies=lambda: False,
        )
        assert not absent(PROJECT)
    finally:
        service.close()
        x.stores.close()


async def test_a_project_from_before_this_change_is_filled_when_the_indexer_looks(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        span = x.ingest("old")
        with x.stores.ledger.engine.begin() as conn:  # sources that never got a job
            conn.execute(delete(embedding_jobs))
        assert x.stores.embedding_index.jobs(PROJECT) == ()
        assert x.stores.embedding_index.needs_indexing(PROJECT)
        await service.auto.run_pass()
        assert states(x) == ["READY"]
        assert x.service.read_vectors(PROJECT, (span,))[0].data
    finally:
        service.close()
        x.stores.close()


async def test_a_failed_source_waits_for_a_retry_instead_of_being_tried_every_minute(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        x.worker.release = None
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        claimed = x.stores.embedding_index.claim(PROJECT, job.job_id, "worker:test", 0)
        x.stores.embedding_index.finish(claimed, "FAILED", "ENCODER_FAILED")
        assert states(x) == ["FAILED"]
        assert not x.stores.embedding_index.needs_indexing(PROJECT)
    finally:
        service.close()
        x.stores.close()


async def test_the_explicit_command_waits_for_the_job_in_hand_and_cancels_nothing(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        x.ingest("b")
        x.worker.release = asyncio.Event()
        background = asyncio.create_task(service.auto.run_pass())
        await asyncio.wait_for(x.worker.entered.wait(), 3)
        assert service.index_busy and not service.prepare_waiting
        generation = x.stores.embedding_index.configuration(PROJECT).generation
        explicit = asyncio.create_task(
            service.prepare(PROJECT, "operation:prepare", generation=generation)
        )
        await asyncio.sleep(0.3)
        assert service.prepare_waiting and not explicit.done()  # it queued; it did not fail as busy
        x.worker.release.set()
        result = await asyncio.wait_for(explicit, 5)
        await asyncio.wait_for(background, 5)
        assert result["state"] == "READY" and states(x) == ["READY", "READY"]
        assert "CANCELLED" not in states(x) and not service.index_busy
    finally:
        service.close()
        x.stores.close()


async def test_drain_asks_whether_to_stop_between_jobs_and_never_inside_one(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path / "workspace")
    try:
        x.ingest("a")
        x.ingest("b")
        ran = await x.service.drain(PROJECT, stop=lambda: len(x.worker.calls) >= 1)
        assert len(ran) == 1 and len(x.worker.calls) == 1
        assert states(x) == ["PENDING", "READY"]
        assert len(await x.service.drain(PROJECT)) == 1  # the rest runs when nothing stops it
    finally:
        x.stores.close()


async def test_a_review_workspace_never_starts_the_background_indexer(tmp_path: Path) -> None:
    source, assets = sample_assets(tmp_path)
    await assets.import_directory(source)
    x = fixture(tmp_path / "workspace")
    service = LocalRetrievalService(
        mode=DeploymentMode.HOSTED_REVIEW,
        assets=assets,
        downloader=FixedModelDownload(assets),
        store=x.stores.embedding_index,
        projects=x.stores.projects,
        artifacts=x.service.artifacts,
        sessions=x.stores.auth,
        governance=x.stores.governance,
        clock=SystemClock(),
        dependency_available=lambda: True,
        factory=lambda _: x.service,
    )
    try:
        service.start_auto_index()
        assert not service.auto.started
    finally:
        service.close()
        x.stores.close()


async def test_two_explicit_commands_still_conflict(tmp_path: Path) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        x.worker.release = asyncio.Event()
        generation = x.stores.embedding_index.configuration(PROJECT).generation
        first = asyncio.create_task(
            service.prepare(PROJECT, "operation:one", generation=generation)
        )
        await asyncio.wait_for(x.worker.entered.wait(), 3)
        with pytest.raises(Exception, match="INDEX_BUSY"):
            await service.prepare(PROJECT, "operation:two", generation=generation)
        x.worker.release.set()
        await asyncio.wait_for(first, 5)
    finally:
        service.close()
        x.stores.close()


async def test_vectors_nothing_refers_to_any_more_are_dropped_after_the_project_is_drained(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        await service.auto.run_pass()
        assert states(x) == ["READY"]
        with x.stores.ledger.engine.begin() as conn:  # a vector left behind by a replaced source
            conn.execute(
                insert(embedding_vectors).values(
                    project_id=PROJECT, content_key="f" * 64, vector_sha256="0" * 64, data=b"x"
                )
            )
        before = len(_vector_keys(x))
        x.ingest("b")
        await service.auto.run_pass()
        keys = _vector_keys(x)
        assert "f" * 64 not in keys and len(keys) == before - 1 + 1
    finally:
        service.close()
        x.stores.close()


def _vector_keys(x: IndexFixture) -> set[str]:
    with x.stores.ledger.engine.begin() as conn:
        return {row[0] for row in conn.execute(select(embedding_vectors.c.content_key))}


async def test_the_background_loop_starts_looks_again_when_kicked_and_stops_with_the_service(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        service.start_auto_index()
        assert service.auto.started
        await asyncio.sleep(0.2)  # the first look finds nothing to do
        x.ingest("a")
        service.kick_auto_index()
        for _ in range(100):
            if states(x) == ["READY"]:
                break
            await asyncio.sleep(0.05)
        assert states(x) == ["READY"]
    finally:
        service.close()
        await asyncio.sleep(0.1)
        assert not service.auto.started
        x.stores.close()


# --- the project-wide read no longer holds the event loop --------------------------------------


async def test_reconcile_in_a_thread_gives_the_same_jobs_and_keeps_the_loop_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = fixture(tmp_path / "workspace")
    try:
        for name in "abcdef":
            x.ingest(name)
        same = x.service.reconcile(PROJECT)
        original: Any = x.service.artifacts.read_evidence

        def slow(span_id: str) -> Any:
            time.sleep(0.1)  # a blocking read, like SQLite for a large project
            return original(span_id)

        monkeypatch.setattr(x.service.artifacts, "read_evidence", slow)
        worst = 0.0
        running = True

        async def tick() -> None:
            nonlocal worst
            last = time.perf_counter()
            while running:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                worst = max(worst, now - last - 0.01)
                last = now

        ticker = asyncio.create_task(tick())
        await asyncio.sleep(0.05)
        worst = 0.0
        ids = await x.service.areconcile(PROJECT)
        running = False
        await ticker
        assert ids == same and len(ids) == 6
        assert worst < 0.15  # the six 0.1 s reads ran in a thread, not on the loop
    finally:
        x.stores.close()


async def test_the_last_read_and_the_publication_of_a_job_do_not_hold_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first read of a job's source and the last read before READY run in a worker thread,
    and the event loop keeps running other coroutines while each of them is in progress.

    Checked by thread identity and by a handshake, not by measuring how long the loop was late:
    the read itself waits (in its thread) until the loop has run another coroutine, which can only
    happen if the loop is free. The per-span checkpoint commit that every job also does stays on
    the loop and may be slow on a slow disk; it is not what this test is about.
    """
    x = fixture(tmp_path / "workspace")
    try:
        x.ingest("a")
        original: Any = getattr(x.service, READ_SOURCE)
        loop_thread = threading.get_ident()
        threads: list[int] = []
        loop_ran_meanwhile: list[bool] = []
        beat = threading.Event()

        def slow(intent: Any) -> Any:
            threads.append(threading.get_ident())
            beat.clear()
            # Blocks this thread, not the loop, unless the read wrongly runs on the loop: then the
            # ticker below cannot run and the wait times out.
            loop_ran_meanwhile.append(beat.wait(5))
            return original(intent)

        monkeypatch.setattr(x.service, READ_SOURCE, slow)

        async def ticker() -> None:
            while True:
                await asyncio.sleep(0)
                beat.set()
                await asyncio.sleep(0.005)

        task = asyncio.create_task(ticker())
        try:
            (ready,) = await x.service.drain(PROJECT)
        finally:
            task.cancel()
        assert ready.state == "READY"
        assert len(threads) == 2  # the first read and the one inside the publication
        assert loop_thread not in threads
        assert loop_ran_meanwhile == [True, True]
    finally:
        x.stores.close()


async def test_a_publication_that_has_started_finishes_even_if_the_caller_is_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = fixture(tmp_path / "workspace")
    try:
        x.ingest("a")
        original: Any = getattr(x.service, READ_SOURCE)
        reads = 0
        publishing = threading.Event()

        def slow(intent: Any) -> Any:
            nonlocal reads
            reads += 1
            if reads == 2:  # the read inside the publication transaction
                publishing.set()
                time.sleep(0.4)
            return original(intent)

        monkeypatch.setattr(x.service, READ_SOURCE, slow)
        task = asyncio.create_task(x.service.drain(PROJECT))
        assert await asyncio.to_thread(publishing.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert states(x) == ["READY"]  # not left half published, and not marked cancelled
    finally:
        x.stores.close()


def test_the_project_wide_evidence_read_happens_once_not_once_per_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = fixture(tmp_path / "workspace")
    try:
        for name in "abcd":
            x.ingest(name)
        calls = 0
        original: Any = x.service.artifacts.list_evidence

        def counting(project: str) -> Any:
            nonlocal calls
            calls += 1
            return original(project)

        monkeypatch.setattr(x.service.artifacts, "list_evidence", counting)
        x.service.reconcile(PROJECT)
        assert calls == 1
    finally:
        x.stores.close()


# --- the publication does not hold the write lock while it reads ---------------------------------


async def test_a_publication_reads_the_source_without_holding_the_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last read of a large source takes seconds. A request on the loop that writes meanwhile
    must go through at once, not wait for the lock and fail with "database is locked" after 5 s."""
    x = fixture(tmp_path / "workspace")
    try:
        x.ingest("a")
        original: Any = getattr(x.service, READ_SOURCE)
        reads = 0
        publishing, written = threading.Event(), threading.Event()

        def slow(intent: Any) -> Any:
            nonlocal reads
            reads += 1
            if reads == 2:  # the read that belongs to the publication
                publishing.set()
                assert written.wait(30)
            return original(intent)

        monkeypatch.setattr(x.service, READ_SOURCE, slow)
        task = asyncio.create_task(x.service.drain(PROJECT))
        assert await asyncio.to_thread(publishing.wait, 30)
        started = time.perf_counter()
        error: Exception | None = None
        try:  # any write another request makes on the loop
            x.stores.embedding_index.invalidate("project:other", "artifact:none")
        except Exception as exc:
            error = exc
        elapsed = time.perf_counter() - started
        written.set()
        (job,) = await task
        assert error is None and elapsed < 2.0
        assert job.state == "READY"
    finally:
        x.stores.close()


@pytest.mark.parametrize("phase", ("after_the_read", "after_the_manifest"))
async def test_a_source_that_changed_after_it_was_read_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    x = fixture(tmp_path / "workspace")
    try:
        span = x.ingest("a")
        store = x.stores.embedding_index
        original: Any = getattr(x.service, READ_SOURCE)
        prepare = store.prepare_publication
        reads = 0

        def detach() -> None:
            # The binding ends while the job is still RUNNING (no invalidation yet), so only a
            # check made after the reads can notice.
            with store.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE source_bindings SET state = 'DETACHED' WHERE artifact_id = ?",
                    (span.artifact_id,),
                )

        def read(intent: Any) -> Any:
            nonlocal reads
            reads += 1
            result = original(intent)
            if phase == "after_the_read" and reads == 2:
                detach()
            return result

        def manifest(job: Any) -> None:
            prepare(job)
            if phase == "after_the_manifest":
                detach()  # what the short write transaction has to catch

        monkeypatch.setattr(x.service, READ_SOURCE, read)
        monkeypatch.setattr(store, "prepare_publication", manifest)
        with pytest.raises(Exception, match="EMBEDDING_SOURCE_CHANGED"):
            await x.service.drain(PROJECT)
        (job,) = store.jobs(PROJECT)
        assert job.state == "STALE" and job.reason == "EMBEDDING_SOURCE_CHANGED"
    finally:
        x.stores.close()


def _lock_probe(path: str) -> bool:
    """Whether another connection can take the write lock right now."""
    connection = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


@pytest.mark.parametrize("operation", ("reconcile", "collect_orphans"))
async def test_reconcile_and_orphan_collection_compare_sources_without_the_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from thoth.adapters.storage import embedding_source

    x = fixture(tmp_path / "workspace")
    try:
        x.ingest("a")
        x.ingest("b")
        store = x.stores.embedding_index
        database = str(store.engine.url.database)
        free: list[bool] = []
        original = embedding_source.matches

        def watched(connection: Any, intent: Any) -> bool:
            free.append(_lock_probe(database))  # comparing a job with its source reads every span
            return original(connection, intent)

        monkeypatch.setattr(embedding_source, "matches", watched)
        getattr(store, operation)(PROJECT)
        assert len(free) >= 2 and all(free)
    finally:
        x.stores.close()


# --- interrupted work is picked up again, errors and people's cancellations are not ---------------


def _fresh_index(x: IndexFixture) -> EmbeddingIndexService:
    """What a restart creates: a new index service over the same stores."""
    return EmbeddingIndexService(
        store=x.stores.embedding_index,
        artifacts=x.service.artifacts,
        governance=x.stores.governance,
        projects=x.stores.projects,
        worker=x.worker,
        uow=x.stores.immediate_uow,
        clock=SystemClock(),
        ids=x.service.ids,
    )


def _reopened(x: IndexFixture, old: LocalRetrievalService) -> LocalRetrievalService:
    index = _fresh_index(x)
    return LocalRetrievalService(
        mode=DeploymentMode.LOCAL,
        assets=old.assets,
        downloader=old.downloader,
        store=x.stores.embedding_index,
        projects=x.stores.projects,
        artifacts=x.service.artifacts,
        sessions=x.stores.auth,
        governance=x.stores.governance,
        clock=SystemClock(),
        dependency_available=lambda: True,
        factory=lambda _: index,
    )


async def test_a_job_cut_off_by_the_limit_of_a_pass_waits_again_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.worker.budget = x.worker.budget.model_copy(update={"job_timeout_ms": 3000})
        x.service.configure(PROJECT, True, 1)
        x.ingest("a")
        x.ingest("b")
        stuck = True
        original = x.worker.encode_one

        async def encode(text: str, **kwargs: Any) -> Any:
            if stuck and "b:" in text:  # one source is slow enough to use the whole pass
                await asyncio.sleep(60)
            return await original(text, **kwargs)

        monkeypatch.setattr(x.worker, "encode_one", encode)
        await service.auto.run_pass()
        first = states(x)
        assert "FAILED" not in first and "CANCELLED" not in first
        assert "PENDING" in first  # the cut-off job (and anything behind it) waits
        assert x.stores.embedding_index.needs_indexing(PROJECT)
        stuck = False
        await service.auto.run_pass()
        assert states(x) == ["READY", "READY"]
    finally:
        service.close()
        x.stores.close()


async def test_a_job_stopped_by_closing_the_service_is_picked_up_after_a_restart(
    tmp_path: Path,
) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        x.worker.release = asyncio.Event()
        background = asyncio.create_task(service.auto.run_pass())
        await asyncio.wait_for(x.worker.entered.wait(), 5)
        service.close()  # the application is shutting down, nobody cancelled the job
        await asyncio.gather(background, return_exceptions=True)
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        assert (job.state, job.reason) == ("CANCELLED", "SERVICE_CLOSED")  # not USER_CANCELLED
        x.worker.release.set()
        again = _reopened(x, service)
        try:
            await again.auto.run_pass()
            assert states(x) == ["READY"]
        finally:
            again.close()
    finally:
        x.stores.close()


async def test_a_job_a_crash_left_running_is_picked_up_without_a_click(tmp_path: Path) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        x.ingest("b")
        first, second = x.stores.embedding_index.jobs(PROJECT)
        # Started long ago by a process that is gone: its lease ended, nothing finished it.
        x.stores.embedding_index.claim(PROJECT, first.job_id, "worker:gone", 0)
        # The same loss, already recorded as it is when a person pressed "prepare" first.
        claimed = x.stores.embedding_index.claim(PROJECT, second.job_id, "worker:gone", 0)
        assert x.stores.embedding_index.recover(PROJECT, 10**13) == 2
        assert (
            sorted(str(j.reason) for j in x.stores.embedding_index.jobs(PROJECT))
            == ["LEASE_EXPIRED_NO_AUTORETRY"] * 2
        )
        assert claimed.job_id == second.job_id
        await service.auto.run_pass()
        assert states(x) == ["READY", "READY"]
    finally:
        service.close()
        x.stores.close()


async def test_a_running_job_with_a_lease_that_has_not_ended_is_left_alone(tmp_path: Path) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        x.stores.embedding_index.claim(PROJECT, job.job_id, "worker:live", 10**13)
        await service.auto.run_pass()
        assert states(x) == ["RUNNING"] and x.worker.calls == []
    finally:
        service.close()
        x.stores.close()


async def test_a_job_a_person_cancelled_is_not_started_again_by_itself(tmp_path: Path) -> None:
    x, service = await setup(tmp_path)
    try:
        x.ingest("a")
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        x.stores.embedding_index.claim(PROJECT, job.job_id, "worker:test", 0)
        x.stores.embedding_index.cancel(PROJECT, job.job_id)
        assert states(x) == ["CANCELLED"]
        await service.auto.run_pass()
        assert states(x) == ["CANCELLED"] and x.worker.calls == []
    finally:
        service.close()
        x.stores.close()
