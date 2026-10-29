"""Lease/CAS publication of rebuildable project-local embeddings on the shared DB."""

from collections.abc import Callable
from typing import Literal

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.dialects.sqlite import insert

from thoth.adapters.storage import embedding_source as source
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
    embedding_key,
)


class SqliteEmbeddingIndex:
    def __init__(self, engine: Engine, *, fault: Callable[[str], None] | None = None) -> None:
        self.engine = engine
        self.uow = SqliteAtomicUnitOfWork(engine, immediate=True)
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
            if identifier and source.matches(c, intent):
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
    def _job(row: object) -> EmbeddingJob:
        from typing import cast

        from sqlalchemy.engine import RowMapping

        r = cast(RowMapping, row)
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

    def _current(self, c: object, project: str, job_id: str) -> EmbeddingJob:
        from typing import cast

        from sqlalchemy import Connection

        row = (
            cast(Connection, c)
            .execute(
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

    def _lease(self, c: object, job: EmbeddingJob, now_ms: int) -> None:
        from typing import cast

        from sqlalchemy import Connection

        current = (
            cast(Connection, c)
            .execute(
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
        if not source.header_matches(cast(Connection, c), job.intent):
            raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")

    def checkpoint(
        self, job: EmbeddingJob, text_sha: str, vector: EmbeddingVector, now_ms: int
    ) -> None:
        with self.uow.transaction(), write_connection(self.engine) as c:
            self._lease(c, job, now_ms)
            if text_sha not in {r.text_sha256 for r in job.intent.refs}:
                raise EmbeddingIndexError("EMBEDDING_TEXT_NOT_IN_JOB")
            key = embedding_key(text_sha, job.intent.model)
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

    def cached(self, project: str, text_sha: str) -> EmbeddingVector | None:
        with read_connection(self.engine) as c:
            config = source.configuration(c, project)
            if not config.enabled:
                return None
            row = (
                c.execute(
                    select(embedding_vectors).where(
                        embedding_vectors.c.project_id == project,
                        embedding_vectors.c.content_key == embedding_key(text_sha, config.model),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            vector = EmbeddingVector(bytes(row["data"]))
            if vector.sha256 != row["vector_sha256"]:
                raise EmbeddingIndexError("EMBEDDING_CACHE_CORRUPT")
            return vector

    def publish(self, job: EmbeddingJob, now_ms: int) -> EmbeddingJob:
        with self.uow.transaction(), write_connection(self.engine) as c:
            self._lease(c, job, now_ms)
            if not source.matches(c, job.intent):
                raise EmbeddingIndexError("EMBEDDING_SOURCE_CHANGED")
            digest = self._manifest(job)
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

    def invalidate(self, project: str, artifact: str) -> None:
        with write_connection(self.engine) as c:
            source.invalidate(c, project, artifact)

    def cancel(self, project: str, job_id: str) -> None:
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
                    reason="USER_CANCELLED",
                )
            )

    def is_current(self, intent: EmbeddingIntent) -> bool:
        with read_connection(self.engine) as c:
            return source.matches(c, intent)

    def reconcile(self, project: str) -> int:
        changed = 0
        with self.uow.transaction(), write_connection(self.engine) as c:
            for job in self.jobs(project):
                if job.state in ("PENDING", "RUNNING", "READY") and not source.matches(
                    c, job.intent
                ):
                    changed += c.execute(
                        update(embedding_jobs)
                        .where(
                            embedding_jobs.c.job_id == job.job_id,
                            embedding_jobs.c.generation == job.generation,
                        )
                        .values(
                            state="STALE",
                            generation=job.generation + 1,
                            owner=None,
                            lease_until_ms=None,
                            manifest_digest=None,
                            reason="SOURCE_INVALIDATED",
                        )
                    ).rowcount
        return changed

    def _manifest(self, job: EmbeddingJob) -> str:
        entries: list[tuple[str, str, str]] = []
        for ref in job.intent.refs:
            vector = self.cached(job.intent.project_id, ref.text_sha256)
            if vector is None:
                raise EmbeddingIndexError("EMBEDDING_INDEX_INCOMPLETE")
            entries.append((ref.span_id, ref.text_sha256, vector.sha256))
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
        with self.uow.transaction(), write_connection(self.engine) as c:
            jobs = self.jobs(project)
            if any(j.state == "RUNNING" for j in jobs):
                return 0
            retained = {
                embedding_key(ref.text_sha256, j.intent.model)
                for j in jobs
                if j.state in ("PENDING", "RUNNING", "READY", "FAILED")
                and source.matches(c, j.intent)
                for ref in j.intent.refs
            }
            statement = delete(embedding_vectors).where(embedding_vectors.c.project_id == project)
            if retained:
                statement = statement.where(embedding_vectors.c.content_key.not_in(retained))
            return c.execute(statement).rowcount
