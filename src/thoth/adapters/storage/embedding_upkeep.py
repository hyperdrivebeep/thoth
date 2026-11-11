"""Maintenance of the embedding job table: marking stale jobs, taking up interrupted ones again,
dropping vectors no job refers to. Each reads first (comparing a job with its source reads every
span) and takes the database write lock only for the short write that follows."""

from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from thoth.adapters.storage import embedding_source as source
from thoth.adapters.storage.embedding_schema import embedding_jobs, embedding_vectors
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.embedding_index import SERVICE_CLOSED_REASON, content_key, embedding_key
from thoth.domain.retrieval_embedding import MAX_PASSAGE_WINDOWS, window_count

if TYPE_CHECKING:
    from thoth.adapters.storage.embedding_index import SqliteEmbeddingIndex


def interrupt(index: "SqliteEmbeddingIndex", project: str, job_id: str) -> None:
    """A running job was stopped by the time limit of a pass: it waits again.

    Its stored vectors are kept, so the next run resumes where this one stopped.
    """
    with write_connection(index.engine) as c:
        c.execute(
            update(embedding_jobs)
            .where(
                embedding_jobs.c.project_id == project,
                embedding_jobs.c.job_id == job_id,
                embedding_jobs.c.state == "RUNNING",
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


def resume_interrupted(index: "SqliteEmbeddingIndex", project: str, now_ms: int) -> int:
    """Queue again the jobs the system itself stopped: left RUNNING by a crash once their lease
    ended, already marked FAILED for that reason, or cancelled by a shutdown. Real failures and
    what a person cancelled are not touched."""
    interrupted = (
        ((embedding_jobs.c.state == "RUNNING") & (embedding_jobs.c.lease_until_ms <= now_ms))
        | (
            (embedding_jobs.c.state == "FAILED")
            & (embedding_jobs.c.reason == "LEASE_EXPIRED_NO_AUTORETRY")
        )
        | (
            (embedding_jobs.c.state == "CANCELLED")
            & (embedding_jobs.c.reason == SERVICE_CLOSED_REASON)
        )
    )
    with read_connection(index.engine) as c:
        found = c.execute(
            select(embedding_jobs.c.job_id)
            .where(embedding_jobs.c.project_id == project, interrupted)
            .limit(1)
        ).first()
        if found is None or not source.configuration(c, project).enabled:
            return 0
    with write_connection(index.engine) as c:
        return c.execute(
            update(embedding_jobs)
            .where(embedding_jobs.c.project_id == project, interrupted)
            .values(
                state="PENDING",
                generation=embedding_jobs.c.generation + 1,
                owner=None,
                lease_until_ms=None,
                deadline_ms=None,
                manifest_digest=None,
                reason=None,
            )
        ).rowcount


def reconcile(index: "SqliteEmbeddingIndex", project: str) -> int:
    # Comparing a job with its source reads every span of the source, which takes seconds for a
    # large project. That is done first, without the write lock; the lock is taken only to mark
    # the jobs found stale, and only if a job is still as it was when it was read.
    jobs = index.jobs(project)
    with read_connection(index.engine) as c:
        stale = [
            job
            for job in jobs
            if job.state in ("PENDING", "RUNNING", "READY") and not source.matches(c, job.intent)
        ]
    changed = 0
    if not stale:
        return changed
    with index.uow.transaction(), write_connection(index.engine) as c:
        for job in stale:
            changed += c.execute(
                update(embedding_jobs)
                .where(
                    embedding_jobs.c.job_id == job.job_id,
                    embedding_jobs.c.generation == job.generation,
                    embedding_jobs.c.state.in_(("PENDING", "RUNNING", "READY")),
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


def collect_orphans(index: "SqliteEmbeddingIndex", project: str) -> int:
    # Deciding which vectors are still used compares every job with its source (all spans).
    # That is read first, without the write lock. The lock is taken only to delete, and only
    # if no job changed meanwhile: a job that started running could have stored new vectors.
    jobs = index.jobs(project)
    if any(j.state == "RUNNING" for j in jobs):
        return 0
    retained: set[str] = set()
    with read_connection(index.engine) as c:
        # The first window of a long passage records its size, which says how many more
        # windows there are and so which keys must stay.
        sizes = {
            row[0]: row[1]
            for row in c.execute(
                select(embedding_vectors.c.content_key, embedding_vectors.c.source_tokens).where(
                    embedding_vectors.c.project_id == project,
                    embedding_vectors.c.source_tokens.is_not(None),
                )
            )
        }
        for j in jobs:
            if j.state not in ("PENDING", "RUNNING", "READY", "FAILED") or not source.matches(
                c, j.intent
            ):
                continue
            for ref in j.intent.refs:
                first = embedding_key(ref.text_sha256, j.intent.model)
                retained.add(first)
                for window in range(1, min(window_count(sizes.get(first)), MAX_PASSAGE_WINDOWS)):
                    retained.add(content_key(ref.text_sha256, j.intent.model, window))
    seen = {(j.job_id, j.state, j.generation) for j in jobs}
    with index.uow.transaction(), write_connection(index.engine) as c:
        if {(j.job_id, j.state, j.generation) for j in index.jobs(project)} != seen:
            return 0  # something moved; the next collection looks again
        statement = delete(embedding_vectors).where(embedding_vectors.c.project_id == project)
        if retained:
            statement = statement.where(embedding_vectors.c.content_key.not_in(retained))
        return c.execute(statement).rowcount
