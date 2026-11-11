"""Lease/CAS publication of rebuildable project-local embeddings on the shared DB."""

from collections.abc import Callable
from typing import Literal

from sqlalchemy import Connection, Engine, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import RowMapping

from thoth.adapters.storage import embedding_source as source
from thoth.adapters.storage import embedding_upkeep as upkeep
from thoth.adapters.storage.embedding_schema import (
    embedding_configs,
    embedding_jobs,
    embedding_vectors,
)
from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    has_ambient_transaction,
    read_connection,
    write_connection,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.embedding_index import (
    EmbeddingIndexConfig,
    EmbeddingIndexError,
    EmbeddingIntent,
    EmbeddingJob,
    EmbeddingVector,
    content_key,
)
from thoth.domain.retrieval_embedding import window_count


class SqliteEmbeddingIndex:
    def __init__(self, engine: Engine, *, fault: Callable[[str], None] | None = None) -> None:
        self.engine = engine
        self.uow = SqliteAtomicUnitOfWork(engine, immediate=True)
        # Manifests read ahead of publishing, by (job, generation, owner) of one run of a job.
        self._prepared: dict[tuple[str, int, str | None], str] = {}
        self.fault = fault

    def _fault(self, point: str) -> None:
        if self.fault is not None:
            self.fault(point)

    def assert_worker_boundary(self) -> None:
        if has_ambient_transaction():
            raise EmbeddingIndexError("EMBEDDING_WORKER_INSIDE_TRANSACTION")

    def configuration(self, project: str) -> EmbeddingIndexConfig:
        with read_connection(self.engine) as c:
            return source.configuration(c, project)

    def configure(
        self, value: EmbeddingIndexConfig, expected_generation: int
    ) -> EmbeddingIndexConfig:
        with self.uow.transaction(), write_connection(self.engine) as c:
            old = source.configuration(c, value.project_id)
            if old.generation != expected_generation:
                raise EmbeddingIndexError("EMBEDDING_CONFIG_CONFLICT")
            value = value.model_copy(update={"generation": expected_generation + 1})
            c.execute(
                insert(embedding_configs)
                .values(
                    project_id=value.project_id,
                    generation=value.generation,
                    enabled=int(value.enabled),
                    content_json=value.model_dump_json(),
                )
                .on_conflict_do_update(
                    index_elements=["project_id"],
                    set_={
                        "generation": value.generation,
                        "enabled": int(value.enabled),
                        "content_json": value.model_dump_json(),
                    },
                )
            )
            c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.project_id == value.project_id,
                    embedding_jobs.c.state.in_(("PENDING", "RUNNING", "READY")),
                )
                .values(
                    state="CANCELLED",
                    generation=embedding_jobs.c.generation + 1,
                    owner=None,
                    lease_until_ms=None,
                    manifest_digest=None,
                    reason="CONFIG_CHANGED",
                )
            )
            return value

    def stage_intent(self, intent: EmbeddingIntent) -> str:
        with write_connection(self.engine) as c:
            config = source.configuration(c, intent.project_id)
            if config.generation != intent.config_generation or config.model != intent.model:
                raise EmbeddingIndexError("EMBEDDING_CONFIG_CONFLICT")
            identifier = source.stage(c, intent)
            # Comparing with the source reads all its spans, so only a job that is waiting to be
            # revived (STALE) is compared, not every job that is merely confirmed as present.
            state = (
                c.execute(
                    select(embedding_jobs.c.state).where(embedding_jobs.c.job_id == identifier)
                ).scalar()
                if identifier
                else None
            )
            if state == "STALE" and source.matches(c, intent):
                # Only explicit reconciliation can revive invalidated links; failed/cancelled
                # attempts still require the separate generation-fenced retry command.
                c.execute(
                    update(embedding_jobs)
                    .where(
                        embedding_jobs.c.job_id == identifier,
                        embedding_jobs.c.state == "STALE",
                    )
                    .values(
                        state="PENDING",
                        generation=embedding_jobs.c.generation + 1,
                        owner=None,
                        lease_until_ms=None,
                        deadline_ms=None,
                        manifest_digest=None,
                        reason=None,
                    )
                )
            return identifier

    @staticmethod
    def _job(r: RowMapping) -> EmbeddingJob:
        return EmbeddingJob.model_validate(
            {
                "job_id": r["job_id"],
                "intent": EmbeddingIntent.model_validate_json(r["intent_json"]),
                **{
                    k: r[k]
                    for k in [
                        "state",
                        "generation",
                        "owner",
                        "lease_until_ms",
                        "deadline_ms",
                        "reason",
                        "manifest_digest",
                    ]
                },
            }
        )

    def jobs(self, project: str) -> tuple[EmbeddingJob, ...]:
        with read_connection(self.engine) as c:
            return tuple(
                self._job(r)
                for r in c.execute(
                    select(embedding_jobs)
                    .where(embedding_jobs.c.project_id == project)
                    .order_by(embedding_jobs.c.job_id)
                ).mappings()
            )

    def _current(self, c: Connection, project: str, job_id: str) -> EmbeddingJob:
        row = (
            c.execute(
                select(embedding_jobs).where(
                    embedding_jobs.c.project_id == project, embedding_jobs.c.job_id == job_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise EmbeddingIndexError("EMBEDDING_JOB_NOT_FOUND")
        return self._job(row)

    def claim(self, project: str, job_id: str, owner: str, now_ms: int) -> EmbeddingJob:
        self.assert_worker_boundary()
        with self.uow.transaction(), write_connection(self.engine) as c:
            job = self._current(c, project, job_id)
            if job.state != "PENDING":
                raise EmbeddingIndexError("EMBEDDING_JOB_NOT_PENDING")
            if not source.matches(c, job.intent):
                raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
            budget = source.configuration(c, project).budget
            c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.job_id == job_id,
                    embedding_jobs.c.generation == job.generation,
                    embedding_jobs.c.state == "PENDING",
                )
                .values(
                    state="RUNNING",
                    owner=owner,
                    lease_until_ms=now_ms + budget.lease_ms,
                    deadline_ms=now_ms + budget.job_timeout_ms,
                    reason=None,
                )
            )
            return self._current(c, project, job_id)

    def _lease(self, c: Connection, job: EmbeddingJob, now_ms: int) -> None:
        current = (
            c.execute(
                select(
                    embedding_jobs.c.state,
                    embedding_jobs.c.generation,
                    embedding_jobs.c.owner,
                    embedding_jobs.c.lease_until_ms,
                    embedding_jobs.c.deadline_ms,
                ).where(
                    embedding_jobs.c.project_id == job.intent.project_id,
                    embedding_jobs.c.job_id == job.job_id,
                )
            )
            .mappings()
            .first()
        )
        if (
            current is None
            or current["state"] != "RUNNING"
            or current["generation"] != job.generation
            or current["owner"] != job.owner
        ):
            raise EmbeddingIndexError("EMBEDDING_LEASE_LOST")
        if (
            current["lease_until_ms"] is None
            or now_ms >= current["lease_until_ms"]
            or current["deadline_ms"] is None
            or now_ms >= current["deadline_ms"]
        ):
            raise EmbeddingIndexError("EMBEDDING_LEASE_EXPIRED")
        if not source.header_matches(c, job.intent):
            raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")

    def checkpoint(
        self,
        job: EmbeddingJob,
        text_sha: str,
        vector: EmbeddingVector,
        now_ms: int,
        window: int = 0,
    ) -> None:
        with self.uow.transaction(), write_connection(self.engine) as c:
            self._lease(c, job, now_ms)
            if text_sha not in {r.text_sha256 for r in job.intent.refs}:
                raise EmbeddingIndexError("EMBEDDING_TEXT_NOT_IN_JOB")
            key = content_key(text_sha, job.intent.model, window)
            project = job.intent.project_id
            old = (
                c.execute(
                    select(embedding_vectors).where(
                        embedding_vectors.c.project_id == project,
                        embedding_vectors.c.content_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if old is not None and (
                old["vector_sha256"] != vector.sha256 or bytes(old["data"]) != vector.data
            ):
                raise EmbeddingIndexError("EMBEDDING_CACHE_CONFLICT")
            c.execute(
                insert(embedding_vectors)
                .values(
                    project_id=project,
                    content_key=key,
                    vector_sha256=vector.sha256,
                    data=vector.data,
                    truncated=None if vector.truncated is None else int(vector.truncated),
                    source_tokens=vector.source_tokens,
                )
                .on_conflict_do_nothing(index_elements=["project_id", "content_key"])
            )
            budget = source.configuration(c, project).budget
            c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.job_id == job.job_id,
                    embedding_jobs.c.generation == job.generation,
                )
                .values(lease_until_ms=now_ms + budget.lease_ms)
            )
            self._fault("after_vector_stage")

    def cached(self, project: str, text_sha: str, window: int = 0) -> EmbeddingVector | None:
        with read_connection(self.engine) as c:
            config = source.configuration(c, project)
            if not config.enabled:
                return None
            return self._vector(c, project, content_key(text_sha, config.model, window))

    @staticmethod
    def _vector(c: Connection, project: str, key: str) -> EmbeddingVector | None:
        row = (
            c.execute(
                select(embedding_vectors).where(
                    embedding_vectors.c.project_id == project,
                    embedding_vectors.c.content_key == key,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        vector = EmbeddingVector(
            bytes(row["data"]),
            truncated=None if row["truncated"] is None else bool(row["truncated"]),
            source_tokens=row["source_tokens"],
        )
        if vector.sha256 != row["vector_sha256"]:
            raise EmbeddingIndexError("EMBEDDING_CACHE_CORRUPT")
        return vector

    def cached_windows(self, project: str, text_sha: str) -> tuple[EmbeddingVector, ...] | None:
        """Every window of a passage, in order, or None when any window is missing.

        The first window's recorded size says how many windows exist.
        """
        with read_connection(self.engine) as c:
            config = source.configuration(c, project)
            if not config.enabled:
                return None
            first = self._vector(c, project, content_key(text_sha, config.model, 0))
            if first is None:
                return None
            windows = [first]
            for window in range(1, window_count(first.source_tokens)):
                vector = self._vector(c, project, content_key(text_sha, config.model, window))
                if vector is None:
                    return None
                windows.append(vector)
            return tuple(windows)

    def prepare_publication(self, job: EmbeddingJob) -> None:
        """Do the slow reads of publishing a job without holding the database write lock.

        Compares the job with every span of its source and reads the stored vectors for the
        manifest (seconds for a large source). The result is remembered for this run of the job, so
        that publish only has to check, inside its short write transaction, that the source is
        still the one that was read (same configuration, binding, latest version and hash, which
        fixes the spans). Raises what publish would raise for a source that no longer matches.
        """
        with read_connection(self.engine) as c:
            if not source.matches(c, job.intent):
                raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
        self._prepared[(job.job_id, job.generation, job.owner)] = self._manifest(job)

    def publish(self, job: EmbeddingJob, now_ms: int) -> EmbeddingJob:
        prepared = self._prepared.pop((job.job_id, job.generation, job.owner), None)
        with self.uow.transaction(), write_connection(self.engine) as c:
            self._lease(c, job, now_ms)
            if prepared is None:
                if not source.matches(c, job.intent):
                    raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
                digest = self._manifest(job)
            else:
                # The slow comparison was done in prepare_publication; _lease above has just
                # checked, under the write lock, that the source is still the one that was read.
                digest = prepared
            self._fault("before_ready_cas")
            changed = c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.job_id == job.job_id,
                    embedding_jobs.c.state == "RUNNING",
                    embedding_jobs.c.generation == job.generation,
                    embedding_jobs.c.owner == job.owner,
                )
                .values(
                    state="READY",
                    owner=None,
                    lease_until_ms=None,
                    manifest_digest=digest,
                    reason=None,
                )
            ).rowcount
            if changed != 1:
                raise EmbeddingIndexError("EMBEDDING_READY_CONFLICT")
            self._fault("after_ready_cas")
            return self._current(c, job.intent.project_id, job.job_id)

    def finish(
        self, job: EmbeddingJob, state: Literal["FAILED", "CANCELLED", "STALE"], reason: str
    ) -> None:
        with write_connection(self.engine) as c:
            c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.job_id == job.job_id,
                    embedding_jobs.c.project_id == job.intent.project_id,
                    embedding_jobs.c.state == "RUNNING",
                    embedding_jobs.c.generation == job.generation,
                    embedding_jobs.c.owner == job.owner,
                )
                .values(
                    state=state,
                    reason=reason[:100],
                    owner=None,
                    lease_until_ms=None,
                    manifest_digest=None,
                )
            )

    def retry(self, project: str, job_id: str, generation: int) -> EmbeddingJob:
        with self.uow.transaction(), write_connection(self.engine) as c:
            job = self._current(c, project, job_id)
            if job.generation != generation or job.state not in ("FAILED", "CANCELLED"):
                raise EmbeddingIndexError("EMBEDDING_RETRY_CONFLICT")
            if not source.matches(c, job.intent):
                raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
            c.execute(
                update(embedding_jobs)
                .where(embedding_jobs.c.job_id == job_id, embedding_jobs.c.generation == generation)
                .values(
                    state="PENDING",
                    generation=generation + 1,
                    owner=None,
                    lease_until_ms=None,
                    deadline_ms=None,
                    reason=None,
                )
            )
            return self._current(c, project, job_id)

    def recover(self, project: str, now_ms: int) -> int:
        self.assert_worker_boundary()
        with write_connection(self.engine) as c:
            return c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.project_id == project,
                    embedding_jobs.c.state == "RUNNING",
                    embedding_jobs.c.lease_until_ms <= now_ms,
                )
                .values(
                    state="FAILED",
                    generation=embedding_jobs.c.generation + 1,
                    owner=None,
                    lease_until_ms=None,
                    reason="LEASE_EXPIRED_NO_AUTORETRY",
                )
            ).rowcount

    def interrupt(self, project: str, job_id: str) -> None:
        """A running job was stopped by the system (time limit of a pass): it waits again."""
        upkeep.interrupt(self, project, job_id)

    def resume_interrupted(self, project: str, now_ms: int) -> int:
        """Queue again the jobs the system itself stopped (a crash, a shutdown)."""
        return upkeep.resume_interrupted(self, project, now_ms)

    def invalidate(self, project: str, artifact: str) -> None:
        with write_connection(self.engine) as c:
            source.invalidate(c, project, artifact)

    def cancel(self, project: str, job_id: str, reason: str = "USER_CANCELLED") -> None:
        """Cancel a waiting or running job. The reason tells a person's decision (the default)
        from a stop the system made, which resume_interrupted takes up again."""
        with write_connection(self.engine) as c:
            c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.project_id == project,
                    embedding_jobs.c.job_id == job_id,
                    embedding_jobs.c.state.in_(("PENDING", "RUNNING")),
                )
                .values(
                    state="CANCELLED",
                    generation=embedding_jobs.c.generation + 1,
                    owner=None,
                    lease_until_ms=None,
                    manifest_digest=None,
                    reason=reason,
                )
            )

    def is_current(self, intent: EmbeddingIntent) -> bool:
        with read_connection(self.engine) as c:
            return source.matches(c, intent)

    def is_current_header(self, intent: EmbeddingIntent) -> bool:
        """The same question as is_current without reading the source's spans.

        Configuration, active binding and the latest version's identity and hash. A source whose
        hash and version are unchanged has the same spans, and a request that scores against the
        index checks every span's text hash again, so this is enough to decide whether an index
        is worth using for a question.
        """
        with read_connection(self.engine) as c:
            return source.header_matches(c, intent)

    def needs_indexing(self, project: str) -> bool:
        """Whether the project is on and has a source with pending work or no job yet."""
        jobs = self.jobs(project)
        with read_connection(self.engine) as c:
            return source.needs_indexing(c, project, jobs)

    def index_stamp(self, project: str) -> tuple[bool, int, tuple[tuple[str, int], ...]]:
        """What a resident copy of the READY index depends on, read without parsing any job.

        It changes when the project is switched on or off or reconfigured, when a source is indexed
        (a new READY job) and when one leaves READY (STALE, cancelled, retried).
        """
        with read_connection(self.engine) as c:
            config = source.configuration(c, project)
            rows = c.execute(
                select(embedding_jobs.c.job_id, embedding_jobs.c.generation)
                .where(embedding_jobs.c.project_id == project, embedding_jobs.c.state == "READY")
                .order_by(embedding_jobs.c.job_id)
            ).all()
        return config.enabled, config.generation, tuple((str(r[0]), int(r[1])) for r in rows)

    def reconcile(self, project: str) -> int:
        return upkeep.reconcile(self, project)

    def _manifest(self, job: EmbeddingJob) -> str:
        entries: list[tuple[str, ...]] = []
        for ref in job.intent.refs:
            windows = self.cached_windows(job.intent.project_id, ref.text_sha256)
            if windows is None:
                raise EmbeddingIndexError("EMBEDDING_INDEX_INCOMPLETE")
            # A passage with one window is listed exactly as before; each later window adds its
            # own hash.
            entries.append((ref.span_id, ref.text_sha256, *(w.sha256 for w in windows)))
        return domain_digest(
            "EMBEDDING_READY_MANIFEST",
            "1.0.0",
            canonical_payload({"intent": job.intent, "vectors": entries}),
        )

    def ready(self, project: str, artifact: str, version: str) -> EmbeddingJob | None:
        with read_connection(self.engine) as c:
            rows = c.execute(
                select(embedding_jobs).where(
                    embedding_jobs.c.project_id == project,
                    embedding_jobs.c.artifact_id == artifact,
                    embedding_jobs.c.source_version_id == version,
                    embedding_jobs.c.state == "READY",
                )
            ).mappings()
            for row in rows:
                job = self._job(row)
                if source.matches(c, job.intent):
                    if self._manifest(job) != job.manifest_digest:
                        raise EmbeddingIndexError("EMBEDDING_MANIFEST_CORRUPT")
                    return job
        return None

    def collect_orphans(self, project: str) -> int:
        return upkeep.collect_orphans(self, project)
