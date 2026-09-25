"""Review committed R2 revisions without repeating the external execution on failure."""

from contextlib import suppress

from thoth.application.services.domain_reference_memory import domain_reference_memory
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_basis_capture import select_final_record_producer
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.memory import MemoryRecord
from thoth.domain.post_execution_learning import (
    CommittedExecutionBasis,
    PostExecutionLearningBasis,
    PostExecutionLearningResult,
)
from thoth.domain.project import Project, WorkThread
from thoth.domain.research_execution import ResearchFence, ResearchWork
from thoth.domain.research_request import RevisionRef, ThreadRequestRevision


class PostExecutionMemory:
    def __init__(self, records: RequestRecords, memory: FullProjectMemoryService) -> None:
        self.records, self.memory = records, memory

    def current(self, project_id: str, thread_id: str) -> tuple[PostExecutionLearningResult, ...]:
        results: list[PostExecutionLearningResult] = []
        for key in self.records.ledger.read_heads(project_id):
            if key.startswith(f"THREAD:learning:{thread_id}:"):
                row = self.records.read(project_id, EntityType.THREAD, key.removeprefix("THREAD:"))
                if row is not None:
                    results.append(PostExecutionLearningResult.model_validate(row[1]))
        return tuple(results)

    async def learn(
        self,
        project: Project,
        thread: WorkThread,
        work: ResearchWork,
        execution: CommittedExecutionBasis,
    ) -> PostExecutionLearningResult:
        saved = self.records.read(
            project.project_id, EntityType.THREAD, f"request:{thread.thread_id}"
        )
        if saved is None or saved[0] != work.request_ref:
            raise ResearchFence("POST_EXECUTION_REQUEST_STALE")
        request = ThreadRequestRevision.model_validate(saved[1])
        basis = PostExecutionLearningBasis(
            project_id=project.project_id,
            thread_id=thread.thread_id,
            request_ref=work.request_ref,
            execution=execution,
            policy_digest=request.policy_digest,
            cutoff_at=request.cutoff_at,
            source_refs=tuple(s.span_id for s in work.evidence),
            source_basis_digest=domain_digest(
                "POST_EXECUTION_SOURCES",
                "1.0.0",
                canonical_payload(
                    {
                        "spans": tuple(
                            (s.span_id, s.source_version_id, s.text_sha256) for s in work.evidence
                        )
                    }
                ),
            ),
        )
        digest = domain_digest("POST_EXECUTION_LEARNING", "2.1.0", canonical_payload(basis))
        key = f"learning:{thread.thread_id}:{execution.outcome_revision_ref.revision_digest}"
        prior = self.records.read(project.project_id, EntityType.THREAD, key)
        if prior is not None:
            previous = PostExecutionLearningResult.model_validate(prior[1])
            if previous.basis_digest != digest:
                raise ResearchFence("POST_EXECUTION_BASIS_CHANGED")
            if previous.state == "COMMITTED":
                work.record_refs.append(prior[0])
                select_final_record_producer(work, prior[0])
                return previous
        result = PostExecutionLearningResult(basis=basis, basis_digest=digest, state="PENDING")
        pending_ref: RevisionRef | None = None
        try:
            pending_ref = self.records.save(project.project_id, EntityType.THREAD, key, result)
            work.record_refs.append(pending_ref)
            work.boundary.check()
            candidates: list[MemoryRecord] = []
            heads = set(self.records.ledger.read_heads(project.project_id).values())
            for ref in (execution.outcome_revision_ref, *execution.updated_revision_refs):
                if ref.revision_digest not in heads:
                    raise ValueError("POST_EXECUTION_OWNER_STALE")
                revision = self.records.ledger.read_revision_by_digest(
                    project.project_id, ref.revision_digest
                )
                if revision is None:
                    raise ValueError("POST_EXECUTION_OWNER_MISSING")
                candidate = domain_reference_memory(revision, f"post-memory:{ref.revision_digest}")
                if candidate is not None:
                    candidates.append(candidate)
            memory_basis = self.memory.capture_basis(
                project_id=project.project_id, cutoff_at=project.cutoff_at, scope=thread.scope
            )
            prepared = await self.memory.prepare_thread_results(
                basis=memory_basis, thread_id=thread.thread_id, candidates=tuple(candidates)
            )
            with self.records.ledger.transaction():
                work.boundary.check()
                current = self.records.read(project.project_id, EntityType.THREAD, key)
                if current is None or current[0] != pending_ref:
                    raise ResearchFence("POST_EXECUTION_LEARNING_CHANGED")
                promotion = self.memory.commit_prepared(prepared)
                result = result.model_copy(
                    update={
                        "state": "COMMITTED"
                        if not promotion.held and not promotion.quarantined
                        else "HELD",
                        "memory_revision_refs": tuple(
                            r.revision_digest for r in prepared.revisions
                        ),
                        "review_receipt_refs": tuple(r.receipt_digest for r in promotion.receipts),
                        "promotion": promotion,
                        "reason_code": "MEMORY_REVIEW_HELD"
                        if promotion.held or promotion.quarantined
                        else None,
                    }
                )
                committed_ref = self.records.save(
                    project.project_id, EntityType.THREAD, key, result
                )
            work.record_refs.append(committed_ref)
            select_final_record_producer(work, committed_ref)
            return result
        except Exception as exc:
            result = result.model_copy(
                update={
                    "state": "STALE"
                    if "STALE" in str(exc) or isinstance(exc, ResearchFence)
                    else "HELD",
                    "reason_code": type(exc).__name__ + ":" + str(exc)[:160],
                }
            )
            # This checkpoint reports only learning, never rollback or sandbox retry.
            with suppress(Exception), self.records.ledger.transaction():
                current = self.records.read(project.project_id, EntityType.THREAD, key)
                if (
                    pending_ref is not None
                    and current
                    and current[0] == pending_ref
                    and work.boundary.owns_attempt()
                ):
                    failed_ref = self.records.save(
                        project.project_id, EntityType.THREAD, key, result
                    )
                    work.record_refs.append(failed_ref)
                    select_final_record_producer(work, failed_ref)
            return result
