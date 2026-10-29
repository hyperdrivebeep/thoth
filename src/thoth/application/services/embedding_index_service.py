"""Explicitly enabled indexing: short state transactions around bounded, external encoding."""

import asyncio
import hashlib
from collections.abc import Callable
from typing import Literal

from thoth.domain.auth import current_authenticated_actor
from thoth.domain.embedding_index import (
    EmbeddingIndexConfig,
    EmbeddingIndexError,
    EmbeddingIntent,
    EmbeddingJob,
    EmbeddingVector,
    source_refs,
)
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.embedding_index import BoundedEmbeddingWorkerPort, EmbeddingIndexStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort


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

    def _now(self) -> int:
        return int(self.clock.now().timestamp() * 1000)

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
        for artifact in self.artifacts.list_artifacts(project):
            if artifact.artifact_id not in active:
                continue
            for version in self.artifacts.list_source_versions(project, artifact.artifact_id):
                document = self.artifacts.read_structure(project, artifact.artifact_id, version)
                if document is None:
                    continue
                candidates = tuple(
                    s
                    for s in self.artifacts.list_evidence(project)
                    if s.artifact_id == artifact.artifact_id and s.source_version_id == version
                )
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
        self, project: str, job_id: str, *, deadline: float | None = None
    ) -> EmbeddingJob:
        config = self._owner(project)
        self.store.assert_worker_boundary()
        loop = asyncio.get_running_loop()
        stop_at = loop.time() + config.budget.job_timeout_ms / 1000
        if deadline is not None:
            stop_at = min(stop_at, deadline)
        if loop.time() >= stop_at:
            raise TimeoutError("EMBEDDING_REQUEST_DEADLINE")
        job = self.store.claim(project, job_id, self.ids.new("embedding-worker"), self._now())
        self._active[job_id] = (project, asyncio.current_task())
        try:
            async with asyncio.timeout_at(stop_at):
                spans = self._read_source(job.intent)
                for span in spans:
                    self.store.assert_worker_boundary()
                    vector = self.store.cached(project, span.text_sha256)
                    if vector is None:
                        vector = await self.worker.encode_one(span.exact_text, role="PASSAGE")
                    if self.fault is not None:
                        self.fault("after_encode_before_checkpoint")
                    with self.uow.transaction():
                        self._check_span(job.intent, span)
                        self.store.checkpoint(job, span.text_sha256, vector, self._now())
                with self.uow.transaction():
                    self._read_source(job.intent)
                    return self.store.publish(job, self._now())
        except asyncio.CancelledError:
            if not self._closed:
                self.store.finish(job, "CANCELLED", "CALLER_CANCELLED")
            raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc, EmbeddingIndexError) else type(exc).__name__
            state: Literal["FAILED", "STALE"] = (
                "STALE" if reason == "EMBEDDING_SOURCE_CHANGED" else "FAILED"
            )
            if not self._closed:
                self.store.finish(job, state, reason)
            raise
        finally:
            self._active.pop(job_id, None)

    async def drain(self, project: str) -> tuple[EmbeddingJob, ...]:
        config = self._owner(project)
        deadline = asyncio.get_running_loop().time() + config.budget.job_timeout_ms / 1000
        self.store.assert_worker_boundary()
        self.store.reconcile(project)
        done: list[EmbeddingJob] = []
        for job in self.store.jobs(project):
            if job.state == "PENDING":
                done.append(await self.run_one(project, job.job_id, deadline=deadline))
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

    def read_vectors(
        self, project: str, spans: tuple[EvidenceSpan, ...]
    ) -> tuple[EmbeddingVector, ...]:
        self._actor(project)
        vectors: list[EmbeddingVector] = []
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
                vector = self.store.cached(project, span.text_sha256)
                if vector is None:
                    raise EmbeddingIndexError("INDEX_INCOMPLETE")
                vectors.append(vector)
        return tuple(vectors)

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
                    self.store.cancel(project, job_id)
                finally:
                    if task is not None:
                        task.cancel()
        finally:
            self.worker.close()
