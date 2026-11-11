"""Explicitly enabled indexing: short state transactions around bounded, external encoding."""

import asyncio
import hashlib
from collections.abc import Callable
from typing import Literal

from thoth.domain.auth import current_authenticated_actor
from thoth.domain.embedding_index import (
    SERVICE_CLOSED_REASON,
    EmbeddingIndexConfig,
    EmbeddingIndexError,
    EmbeddingIntent,
    EmbeddingJob,
    EmbeddingVector,
    source_refs,
)
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.retrieval_embedding import window_count
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.embedding_index import (
    BoundedEmbeddingWorkerPort,
    EmbeddingIndexStorePort,
    PreparableEmbeddingWorkerPort,
)
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort


def _require_deadline(stop_at: float) -> None:
    if asyncio.get_running_loop().time() >= stop_at:
        raise TimeoutError("EMBEDDING_REQUEST_DEADLINE")


class EmbeddingIndexService:
    def __init__(
        self,
        *,
        store: EmbeddingIndexStorePort,
        artifacts: ArtifactLedgerPort,
        governance: GovernanceStorePort,
        projects: ProjectStorePort,
        worker: BoundedEmbeddingWorkerPort,
        uow: AtomicUnitOfWorkPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.store, self.artifacts, self.governance, self.projects = (
            store,
            artifacts,
            governance,
            projects,
        )
        self.worker, self.uow, self.clock, self.ids, self.fault = worker, uow, clock, ids, fault
        self._active: dict[str, tuple[str, asyncio.Task[object] | None]] = {}
        self._closed = False
        # Told the project whenever its index may have changed, so a resident copy can be dropped.
        self.on_change: Callable[[str], None] | None = None

    def _now(self) -> int:
        return int(self.clock.now().timestamp() * 1000)

    def _changed(self, project: str) -> None:
        if self.on_change is not None:
            self.on_change(project)

    def _actor(self, project: str, *, manage: bool = False) -> str:
        if self.projects.read(project) is None:
            raise EmbeddingIndexError("EMBEDDING_PROJECT_MISSING")
        actor = current_authenticated_actor()
        if actor is None:
            return "local:operator"
        if actor.project_id != project or ("WRITE" if manage else "READ") not in actor.capabilities:
            raise EmbeddingIndexError("EMBEDDING_PROJECT_SCOPE_DENIED")
        return actor.actor_id

    def configure(
        self, project: str, enabled: bool, expected_generation: int
    ) -> EmbeddingIndexConfig:
        actor = self._actor(project, manage=True)
        value = EmbeddingIndexConfig(
            project_id=project, enabled=enabled, actor_ref=actor, budget=self.worker.budget
        )
        result = self.store.configure(value, expected_generation)
        for p, task in self._active.values():
            if p == project and task is not None:
                task.cancel()
        self._changed(project)
        return result

    def _owner(self, project: str) -> EmbeddingIndexConfig:
        if self._closed:
            raise EmbeddingIndexError("EMBEDDING_SERVICE_CLOSED")
        actor = self._actor(project, manage=True)
        config = self.store.configuration(project)
        if not config.enabled:
            raise EmbeddingIndexError("EMBEDDING_DISABLED")
        if actor != config.actor_ref:
            raise EmbeddingIndexError("EMBEDDING_INDEX_ACTOR_CHANGED")
        if config.budget != self.worker.budget:
            raise EmbeddingIndexError("EMBEDDING_BUDGET_CAPABILITY_MISMATCH")
        return config

    def _read_source(self, intent: EmbeddingIntent) -> tuple[EvidenceSpan, ...]:
        self._owner(intent.project_id)
        if not any(
            b.artifact_id == intent.artifact_id and b.state == "ACTIVE"
            for b in self.governance.list_source_bindings(intent.project_id)
        ):
            raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
        document = self.artifacts.read_structure(
            intent.project_id, intent.artifact_id, intent.source_version_id
        )
        if document is None or document.artifact.byte_sha256 != intent.source_sha256:
            raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
        spans: list[EvidenceSpan] = []
        for ref in intent.refs:
            # Individual access checks also honor independent span grants; no raw fallback.
            span = self.artifacts.read_evidence(ref.span_id)
            if span is None or (
                span.project_id,
                span.artifact_id,
                span.source_version_id,
                span.text_sha256,
            ) != (intent.project_id, intent.artifact_id, intent.source_version_id, ref.text_sha256):
                raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
            if hashlib.sha256(span.exact_text.encode()).hexdigest() != span.text_sha256:
                raise EmbeddingIndexError("EMBEDDING_SOURCE_CORRUPT")
            spans.append(span)
        result = tuple(spans)
        if source_refs(result) != intent.refs:
            raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
        return result

    def _check_span(self, intent: EmbeddingIntent, expected: EvidenceSpan) -> None:
        self._owner(intent.project_id)
        current = self.artifacts.read_evidence(expected.span_id)
        if current != expected:
            raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")

    def reconcile(self, project: str) -> tuple[str, ...]:
        ids = self._reconcile(project)
        self._changed(project)
        return ids

    async def areconcile(self, project: str) -> tuple[str, ...]:
        """reconcile() in a worker thread: it reads every span of the project one by one, which
        took 45 s of event-loop time for a 19,803-span project. The change notice (it touches
        loop-owned state) is given back on the loop."""
        ids = await asyncio.to_thread(self._reconcile, project)
        self._changed(project)
        return ids

    def _reconcile(self, project: str) -> tuple[str, ...]:
        config = self._owner(project)
        ids: list[str] = []
        self.store.assert_worker_boundary()
        self.store.reconcile(project)
        active = {
            b.artifact_id
            for b in self.governance.list_source_bindings(project)
            if b.state == "ACTIVE"
        }
        for job in self.store.jobs(project):
            if job.intent.artifact_id not in active:
                self.store.invalidate(project, job.intent.artifact_id)
        # One read of the project's evidence, grouped by source (it used to be read again for every
        # version of every source). Nothing in the loop below changes the evidence.
        by_source: dict[tuple[str, str], list[EvidenceSpan]] = {}
        for span in self.artifacts.list_evidence(project):
            by_source.setdefault((span.artifact_id, span.source_version_id), []).append(span)
        for artifact in self.artifacts.list_artifacts(project):
            if artifact.artifact_id not in active:
                continue
            for version in self.artifacts.list_source_versions(project, artifact.artifact_id):
                document = self.artifacts.read_structure(project, artifact.artifact_id, version)
                if document is None:
                    continue
                candidates = tuple(by_source.get((artifact.artifact_id, version), ()))
                # Do not index a partially visible source as complete.
                for s in candidates:
                    self.artifacts.read_evidence(s.span_id)
                intent = EmbeddingIntent(
                    project_id=project,
                    artifact_id=artifact.artifact_id,
                    source_version_id=version,
                    source_sha256=document.artifact.byte_sha256,
                    refs=source_refs(candidates),
                    model=config.model,
                    config_generation=config.generation,
                )
                if self.store.is_current(intent):
                    ids.append(self.store.stage_intent(intent))
        return tuple(ids)

    async def run_one(
        self,
        project: str,
        job_id: str,
        *,
        deadline: float | None = None,
        resumable: bool = False,
    ) -> EmbeddingJob:
        """Run one job.

        With resumable, a time limit that comes from deadline (the limit of the whole pass) puts the
        job back to PENDING instead of failing it: its stored vectors are kept and the next pass
        goes on. The job's own time limit still fails it.
        """
        config = self._owner(project)
        self.store.assert_worker_boundary()
        loop = asyncio.get_running_loop()
        job_stop = loop.time() + config.budget.job_timeout_ms / 1000
        stop_at = job_stop
        if deadline is not None:
            stop_at = min(job_stop, deadline)
        if loop.time() >= stop_at:
            raise TimeoutError("EMBEDDING_REQUEST_DEADLINE")
        if isinstance(self.worker, PreparableEmbeddingWorkerPort):
            async with asyncio.timeout_at(stop_at):
                await self.worker.prepare(deadline=stop_at)
            _require_deadline(stop_at)
        job = self.store.claim(project, job_id, self.ids.new("embedding-worker"), self._now())
        self._active[job_id] = (project, asyncio.current_task())
        try:
            async with asyncio.timeout_at(stop_at):
                # Reading every span of a large source is seconds of blocking work.
                spans = await asyncio.to_thread(self._read_source, job.intent)
                _require_deadline(stop_at)
                for span in spans:
                    # A passage longer than one window is stored window by window. The first
                    # window records the passage's size, which says how many windows follow;
                    # each one is checkpointed alone, so an interrupted job resumes after the
                    # last stored window.
                    window, windows = 0, 1
                    while window < windows:
                        self.store.assert_worker_boundary()
                        # Cached work also yields to cancellation and foreground requests.
                        await asyncio.sleep(0)
                        _require_deadline(stop_at)
                        vector = self.store.cached(project, span.text_sha256, window)
                        if vector is None:
                            vector = await self._encode_window(span, window)
                        if window == 0:
                            windows = window_count(vector.source_tokens)
                        if self.fault is not None:
                            self.fault("after_encode_before_checkpoint")
                        with self.uow.transaction():
                            self._check_span(job.intent, span)
                            _require_deadline(stop_at)
                            self._checkpoint(job, span, vector, window)
                            _require_deadline(stop_at)
                        window += 1
                return await self._publish_off_loop(job, stop_at)
        except asyncio.CancelledError:
            if not self._closed:
                self.store.finish(job, "CANCELLED", "CALLER_CANCELLED")
            raise
        except Exception as exc:
            if (
                resumable
                and isinstance(exc, TimeoutError)
                and deadline is not None
                and deadline <= job_stop
                and loop.time() >= stop_at
            ):
                if not self._closed:
                    self.store.interrupt(project, job_id)
                raise
            reason = str(exc) if isinstance(exc, EmbeddingIndexError) else type(exc).__name__
            state: Literal["FAILED", "STALE"] = (
                "STALE" if reason == "EMBEDDING_SOURCE_CHANGED" else "FAILED"
            )
            if not self._closed:
                self.store.finish(job, state, reason)
            raise
        finally:
            self._active.pop(job_id, None)
            self._changed(project)

    async def _publish_off_loop(self, job: EmbeddingJob, stop_at: float) -> EmbeddingJob:
        """The last read of the source and the READY publication, in a thread.

        Reading every span again is seconds of blocking work for a large source (16 s for 12,961
        spans), so it must not run on the event loop, and it must not run while the database write
        lock is held either: the loop is free meanwhile and its own writes would wait for the lock
        (and fail after 5 s). So the source and the stored vectors are read first, with no lock,
        and the short write transaction only checks that the source is still the one that was read
        (same binding, version and hash) before it marks the job READY. A publication that has
        started cannot be interrupted: if the caller is cancelled meanwhile, it finishes first,
        and a job it made READY stays READY (the cancel only marks jobs that are still RUNNING).
        """
        loop = asyncio.get_running_loop()

        def publish() -> EmbeddingJob:
            self._read_source(job.intent)
            self.store.prepare_publication(job)
            if loop.time() >= stop_at:
                raise TimeoutError("EMBEDDING_REQUEST_DEADLINE")
            with self.uow.transaction():
                self._owner(job.intent.project_id)
                published = self.store.publish(job, self._now())
                # Raise inside the publication UoW so an overdue READY rolls back.
                if loop.time() >= stop_at:
                    raise TimeoutError("EMBEDDING_REQUEST_DEADLINE")
                return published

        running = asyncio.ensure_future(asyncio.to_thread(publish))
        try:
            return await asyncio.shield(running)
        except asyncio.CancelledError:
            await asyncio.wait({running})
            raise

    async def drain(
        self,
        project: str,
        *,
        deadline: float | None = None,
        stop: Callable[[], bool] | None = None,
        resumable: bool = False,
    ) -> tuple[EmbeddingJob, ...]:
        """Run the pending jobs in turn. stop() is asked between jobs, never inside one."""
        config = self._owner(project)
        self.store.assert_worker_boundary()
        await asyncio.to_thread(self.store.reconcile, project)
        # The time budget is for running jobs: queueing them (reading every source) is not counted.
        own_deadline = asyncio.get_running_loop().time() + config.budget.job_timeout_ms / 1000
        deadline = min(deadline, own_deadline) if deadline is not None else own_deadline
        done: list[EmbeddingJob] = []
        for job in self.store.jobs(project):
            if job.state == "PENDING":
                if stop is not None and stop():
                    break
                done.append(
                    await self.run_one(project, job.job_id, deadline=deadline, resumable=resumable)
                )
        return tuple(done)

    def recover(self, project: str) -> int:
        self._owner(project)
        return self.store.recover(project, self._now())

    def retry(self, project: str, job_id: str, generation: int) -> EmbeddingJob:
        self._owner(project)
        return self.store.retry(project, job_id, generation)

    def cancel(self, project: str, job_id: str) -> None:
        self._actor(project, manage=True)
        self.store.cancel(project, job_id)
        active = self._active.get(job_id)
        if active is not None and active[0] == project and active[1] is not None:
            active[1].cancel()

    def _checkpoint(
        self, job: EmbeddingJob, span: EvidenceSpan, vector: EmbeddingVector, window: int
    ) -> None:
        # The first window is stored by the call every earlier version made.
        if window == 0:
            self.store.checkpoint(job, span.text_sha256, vector, self._now())
        else:
            self.store.checkpoint(job, span.text_sha256, vector, self._now(), window)

    async def _encode_window(self, span: EvidenceSpan, window: int) -> EmbeddingVector:
        if window == 0:
            return await self.worker.encode_one(span.exact_text, role="PASSAGE")
        return await self.worker.encode_one(span.exact_text, role="PASSAGE", window=window)

    def read_vectors(
        self, project: str, spans: tuple[EvidenceSpan, ...]
    ) -> tuple[EmbeddingVector, ...]:
        """One vector per span: its first window. Every later window must exist as well; use
        read_window_vectors to score a passage by its best window."""
        return tuple(windows[0] for windows in self.read_window_vectors(project, spans))

    def read_window_vectors(
        self, project: str, spans: tuple[EvidenceSpan, ...]
    ) -> tuple[tuple[EmbeddingVector, ...], ...]:
        self._actor(project)
        vectors: list[tuple[EmbeddingVector, ...]] = []
        if connected_retrieval_spans(spans) != spans:
            raise EmbeddingIndexError("EMBEDDING_INELIGIBLE_SOURCE")
        with self.uow.transaction():
            active = {
                b.artifact_id
                for b in self.governance.list_source_bindings(project)
                if b.state == "ACTIVE"
            }
            ready: dict[tuple[str, str], EmbeddingJob] = {}
            for span in spans:
                if span.project_id != project:
                    raise EmbeddingIndexError("EMBEDDING_PROJECT_SCOPE_DENIED")
                if span.artifact_id not in active:
                    raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
                current = self.artifacts.read_evidence(span.span_id)
                if current != span:
                    raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
                if hashlib.sha256(span.exact_text.encode()).hexdigest() != span.text_sha256:
                    raise EmbeddingIndexError("EMBEDDING_SOURCE_CORRUPT")
                key = (span.artifact_id, span.source_version_id)
                job = ready.get(key) or self.store.ready(project, *key)
                if job is None:
                    raise EmbeddingIndexError("INDEX_INCOMPLETE")
                ready[key] = job
                if span.span_id not in {r.span_id for r in job.intent.refs}:
                    raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
                windows = self.store.cached_windows(project, span.text_sha256)
                if windows is None:
                    raise EmbeddingIndexError("INDEX_INCOMPLETE")
                vectors.append(windows)
        return tuple(vectors)

    def ready_sources(self, project: str) -> tuple[tuple[str, str], ...]:
        """The (artifact, version) pairs that have a READY job and a live source binding."""
        self._actor(project)
        active = {
            b.artifact_id
            for b in self.governance.list_source_bindings(project)
            if b.state == "ACTIVE"
        }
        pairs = {
            (job.intent.artifact_id, job.intent.source_version_id)
            for job in self.store.jobs(project)
            if job.state == "READY" and job.intent.artifact_id in active
        }
        return tuple(sorted(pairs))

    def read_source_windows(
        self, project: str, artifact: str, version: str
    ) -> tuple[tuple[str, str, tuple[EmbeddingVector, ...]], ...]:
        """(span, text hash, windows) of every passage of one READY source."""
        self._actor(project)
        job = self.store.ready(project, artifact, version)
        if job is None:
            raise EmbeddingIndexError("INDEX_INCOMPLETE")
        rows: list[tuple[str, str, tuple[EmbeddingVector, ...]]] = []
        for ref in job.intent.refs:
            windows = self.store.cached_windows(project, ref.text_sha256)
            if windows is None:
                raise EmbeddingIndexError("INDEX_INCOMPLETE")
            rows.append((ref.span_id, ref.text_sha256, windows))
        return tuple(rows)

    async def aclose(self) -> None:
        tasks = [t for _, t in self._active.values() if t is not None]
        for task in tasks:
            task.cancel()
        await self.worker.aclose()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)

    def close(self) -> None:
        # Fence database writers before synchronous runtime storage shutdown.
        self._closed = True
        try:
            for job_id, (project, task) in tuple(self._active.items()):
                try:
                    # Shutting down is not a person's decision: the reason says so, and the
                    # background indexer takes the job up again at the next start.
                    self.store.cancel(project, job_id, SERVICE_CLOSED_REASON)
                finally:
                    if task is not None:
                        task.cancel()
        finally:
            self.worker.close()
