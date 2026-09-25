from __future__ import annotations

import re
from collections import Counter
from typing import Literal

from pydantic import AwareDatetime

from thoth.application.services.memory_admission import MemoryAdmissionService
from thoth.application.services.memory_review_service import MemoryReviewService
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.scoped_memory import MemoryResourceAccess
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemoryTransition,
    MemoryTransitionReceipt,
)
from thoth.domain.memory_preparation import (
    FullMemoryPromotionResult,
    MemoryPreparationBasis,
    MemoryPreparationHeadChanged,
    PreparedMemoryPromotion,
)
from thoth.domain.revision import StagedRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import (
    FullMemoryStorePort,
    MemoryEmbeddingPort,
    MemoryProjectionBuilderPort,
    MemoryRerankerPort,
    MemoryReviewerPort,
    MemoryStorePort,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_INJECTION = re.compile(
    r"ignore\s+(?:all|previous)|system\s+prompt|developer\s+message|"
    r"이전\s*명령.*무시|(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s]{8,}",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[0-9A-Za-z가-힣_]{3,}")


class FullProjectMemoryService:
    def __init__(
        self,
        *,
        store: FullMemoryStorePort,
        candidates: MemoryStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        reviewer: MemoryReviewerPort | None = None,
        embedding: MemoryEmbeddingPort | None = None,
        reranker: MemoryRerankerPort | None = None,
        projection_builder: MemoryProjectionBuilderPort | None = None,
        admission: MemoryAdmissionService | None = None,
        resource_access: MemoryResourceAccess | None = None,
    ) -> None:
        self._store = store
        self._candidates = candidates
        self._ledger = ledger
        self._clock = clock
        self._ids = ids
        self._reviewer = reviewer
        self._embedding = embedding
        self._reranker = reranker
        self._projection_builder = projection_builder
        self._reviews = MemoryReviewService(reviewer)
        self._admission = admission
        self._resource_access = resource_access

    def capture_basis(
        self,
        *,
        project_id: str,
        cutoff_at: AwareDatetime,
        scope: dict[str, str],
        actor: ActorRef | None = None,
    ) -> MemoryPreparationBasis:
        if self._admission is None and current_authenticated_actor() is not None:
            raise ValueError("MEMORY_AUTHENTICATED_ADMISSION_REQUIRED")
        return MemoryPreparationBasis(
            project_id=project_id,
            cutoff_at=cutoff_at,
            scope=tuple(sorted(scope.items())),
            actor=actor,
            head_set_digest=head_set_digest(self._ledger.read_heads(project_id)),
            memory_revision_set=tuple(
                sorted(item.revision_digest for item in self._store.list_revisions(project_id))
            ),
            candidate_set_digest=domain_digest(
                "MEMORY_CANDIDATE_SET",
                "1.0.0",
                canonical_payload(
                    {
                        "records": tuple(
                            sorted(
                                (item.memory_id, item.revision_digest)
                                for item in self._candidates.list(project_id)
                            )
                        )
                    }
                ),
            ),
            authority=None
            if self._admission is None
            else self._admission.capture(
                project_id=project_id,
                cutoff_at=cutoff_at,
                scope=scope,
                actor=actor,
            ),
        )

    def require_authority(self, basis: MemoryPreparationBasis) -> None:
        if self._resource_access is not None:
            self._resource_access.require_current_uses(basis.project_id)
        if self._admission is None:
            if current_authenticated_actor() is not None or basis.authority is not None:
                raise ValueError("MEMORY_AUTHENTICATED_ADMISSION_REQUIRED")
            return
        current = self._admission.capture(
            project_id=basis.project_id,
            cutoff_at=basis.cutoff_at,
            scope=dict(basis.scope),
            actor=basis.actor,
        )
        if current != basis.authority:
            raise ValueError("MEMORY_PREPARATION_STALE")

    def require_current(
        self,
        basis: MemoryPreparationBasis,
        *,
        expected_head: str | None = None,
        check_heads: bool = True,
    ) -> None:
        # Authority denial outranks concurrency. Head conflicts preserve semantic branches
        # even when a concurrent successful cycle also appended memory.
        self.require_authority(basis)
        current = self.capture_basis(
            project_id=basis.project_id,
            cutoff_at=basis.cutoff_at,
            scope=dict(basis.scope),
            actor=basis.actor,
        )
        if current.authority != basis.authority:
            raise ValueError("MEMORY_PREPARATION_STALE")
        if check_heads and current.head_set_digest != (expected_head or basis.head_set_digest):
            raise MemoryPreparationHeadChanged("MEMORY_HEAD_CHANGED")
        expected = basis.model_copy(
            update={
                "head_set_digest": current.head_set_digest,
            }
        )
        if current != expected:
            raise ValueError("MEMORY_PREPARATION_STALE")

    async def promote_thread_results(
        self,
        *,
        project_id: str,
        thread_id: str,
        cutoff_at: AwareDatetime,
        memory_ids: tuple[str, ...],
        scope: dict[str, str],
        staged_revisions: tuple[StagedRevision, ...] = (),
    ) -> FullMemoryPromotionResult:
        candidates = {item.memory_id: item for item in self._candidates.list(project_id)}
        prepared = await self.prepare_thread_results(
            basis=self.capture_basis(project_id=project_id, cutoff_at=cutoff_at, scope=scope),
            thread_id=thread_id,
            candidates=tuple(candidates[key] for key in memory_ids if key in candidates),
            staged_revisions=staged_revisions,
        )
        with self._ledger.transaction():
            return self.commit_prepared(prepared)

    async def prepare_thread_results(
        self,
        *,
        basis: MemoryPreparationBasis,
        thread_id: str,
        candidates: tuple[MemoryRecord, ...],
        staged_revisions: tuple[StagedRevision, ...] = (),
    ) -> PreparedMemoryPromotion:
        self.require_current(basis)
        project_id, cutoff_at, scope = basis.project_id, basis.cutoff_at, dict(basis.scope)
        staged_by_digest = {item.revision.revision_digest: item for item in staged_revisions}
        existing_revisions = self._store.list_revisions(project_id)
        visible_existing = tuple(
            r
            for r in existing_revisions
            if self._resource_access is None or self._resource_access.may_read(r)
        )
        existing_by_memory = {item.memory_id: item for item in existing_revisions}
        by_id: dict[str, MemoryRecord] = {}
        for item in candidates:
            if item.memory_id in by_id and by_id[item.memory_id] != item:
                raise ValueError("MEMORY_CANDIDATE_CONFLICT")
            by_id[item.memory_id] = item
        candidates = tuple(by_id.values())
        if any(item.project_id != project_id for item in candidates):
            raise ValueError("MEMORY_CANDIDATE_PROJECT_MISMATCH")
        revisions: list[FullMemoryRevision] = []
        receipts: list[MemoryTransitionReceipt] = []
        for candidate in candidates:
            self.require_current(basis)
            if self._resource_access is not None:
                self._resource_access.require_owner(
                    project_id, candidate.owner_revision_ref, staged_by_digest
                )
            existing = existing_by_memory.get(candidate.memory_id)
            if existing is not None:
                if self._resource_access is not None:
                    self._resource_access.require(existing)
                revisions.append(existing)
                continue
            staged_owner = staged_by_digest.get(candidate.owner_revision_ref)
            owner = (
                staged_owner.revision
                if staged_owner is not None
                else self._ledger.read_revision_by_digest(project_id, candidate.owner_revision_ref)
            )
            snapshot = (
                staged_owner.snapshot
                if staged_owner is not None
                else None
                if owner is None
                else self._ledger.read_snapshot(owner.snapshot_id)
            )
            content_excerpt = self._content_excerpt(
                candidate.source_ref,
                candidate.assertion,
                snapshot,
            )
            query_terms = tuple(sorted(self._tokens(content_excerpt)))
            unsafe = bool(_INJECTION.search(content_excerpt))
            owner_valid = owner is not None and owner.project_id == project_id
            content_reusable = len(query_terms) >= 2
            conflict = self._has_conflict(
                revisions=(*visible_existing, *revisions),
                kind=candidate.kind,
                query_terms=frozenset(query_terms),
                content_excerpt=content_excerpt,
            )
            reviews = await self._reviews.evaluate(
                candidate_digest=candidate.revision_digest,
                owner_revision_ref=candidate.owner_revision_ref,
                source_ref=candidate.source_ref,
                content_excerpt=content_excerpt,
                kind=candidate.kind,
                cutoff_at=cutoff_at,
                scope=scope,
                unsafe=unsafe,
                owner_valid=owner_valid,
                content_reusable=content_reusable,
                conflict=conflict,
                validate_current=lambda: self.require_current(basis),
            )
            self.require_authority(basis)
            transition = self._reduce(reviews)
            recall_eligible = transition == MemoryTransition.COMMIT
            action_eligible = recall_eligible and candidate.kind == MemoryKind.FACT
            now = self._clock.now()
            revision_id = self._ids.new("memory-revision")
            draft: dict[str, object] = {
                "memory_revision_id": revision_id,
                "memory_id": candidate.memory_id,
                "project_id": project_id,
                "origin_thread_id": thread_id,
                "payload_mode": candidate.payload_mode.value,
                "kind": candidate.kind.value,
                "owner_revision_ref": candidate.owner_revision_ref,
                "source_ref": candidate.source_ref,
                "assertion": "[REDACTED_QUARANTINED_MEMORY]" if unsafe else candidate.assertion,
                "content_excerpt": ("[REDACTED_QUARANTINED_MEMORY]" if unsafe else content_excerpt),
                "scope": scope,
                "evidence_refs": () if owner is None else owner.evidence_refs,
                "query_terms": () if unsafe else query_terms,
                "support_status": "SUPPORTED" if owner_valid and not conflict else "CONFLICTING",
                "authority_status": "AUTHORITATIVE" if owner_valid else "UNKNOWN",
                "cutoff_at": cutoff_at,
                "cutoff_valid": owner_valid,
                "reviews": tuple(review.model_dump(mode="json") for review in reviews),
                "transition": transition.value,
                "recall_eligible": recall_eligible,
                "action_eligible": action_eligible,
                "canonical_truth": True,
                "semantic_truth_certified": False,
                "parent_revision_digest": None,
                "created_at": now,
                "schema_version": "2.0.0",
            }
            revision_digest = domain_digest(
                "FULL_MEMORY_REVISION", "2.0.0", canonical_payload(draft)
            )
            revision = FullMemoryRevision.model_validate(
                {**draft, "revision_digest": revision_digest}
            )
            receipt_id = self._ids.new("memory-receipt")
            receipt_draft: dict[str, object] = {
                "receipt_id": receipt_id,
                "project_id": project_id,
                "memory_revision_id": revision_id,
                "transition": transition.value,
                "source_revision_ref": candidate.owner_revision_ref,
                "review_basis_digests": tuple(review.basis_digest for review in reviews),
                "integrity": "VALID",
                "provenance": "OWNER_REVISION_BOUND",
                "authorization": "PROJECT_SCOPED",
                "semantic_truth": "NOT_CERTIFIED",
                "recorded_at": now,
            }
            receipt = MemoryTransitionReceipt.model_validate(
                {
                    **receipt_draft,
                    "receipt_digest": domain_digest(
                        "MEMORY_TRANSITION_RECEIPT",
                        "1.0.0",
                        canonical_payload(receipt_draft),
                    ),
                }
            )
            revisions.append(revision)
            receipts.append(receipt)
        new_revisions = tuple(
            item for item in revisions if item.memory_id not in existing_by_memory
        )
        projections = self.prepare_projections(
            project_id,
            (*existing_revisions, *new_revisions),
            basis=basis,
            staged_revisions=staged_revisions,
        )
        checkpoint = (
            projections[0].rebuild_checkpoint
            if projections
            else domain_digest(
                "MEMORY_PROJECTION_CHECKPOINT",
                "1.0.0",
                canonical_payload({"source_revision_digests": ()}),
            )
        )
        result = FullMemoryPromotionResult(
            committed=tuple(
                item for item in revisions if item.transition == MemoryTransition.COMMIT
            ),
            revised=tuple(item for item in revisions if item.transition == MemoryTransition.REVISE),
            held=tuple(item for item in revisions if item.transition == MemoryTransition.HOLD),
            quarantined=tuple(
                item for item in revisions if item.transition == MemoryTransition.QUARANTINE
            ),
            receipts=tuple(receipts),
            projection_checkpoint=checkpoint,
            projection_state="BUILT" if projections else "DEFERRED_SCOPE",
        )

        return PreparedMemoryPromotion(
            basis=basis,
            candidates=candidates,
            revisions=new_revisions,
            receipts=tuple(receipts),
            projections=projections,
            result=result,
        )

    def commit_prepared(
        self,
        prepared: PreparedMemoryPromotion,
        *,
        expected_head: str | None = None,
    ) -> FullMemoryPromotionResult:
        # Caller owns one short transaction including semantic heads/dependencies.
        with self._ledger.transaction():
            self.require_current(prepared.basis, expected_head=expected_head)
            existing = {
                item.memory_id: item for item in self._candidates.list(prepared.basis.project_id)
            }
            for candidate in prepared.candidates:
                prior = existing.get(candidate.memory_id)
                if prior is not None and prior != candidate:
                    raise ValueError("MEMORY_CANDIDATE_CHANGED")
                if prior is None:
                    self._candidates.add(candidate)
            for revision, receipt in zip(prepared.revisions, prepared.receipts, strict=True):
                self._store.commit_transition(revision, receipt)
            if prepared.result.projection_state == "BUILT":
                self._store.replace_projections(prepared.basis.project_id, prepared.projections)
        return prepared.result

    def build_context(
        self,
        *,
        project_id: str,
        thread_id: str,
        query: str,
        target_use: Literal["WORKING_CONTEXT", "ACTION_CONTEXT"],
        scope: dict[str, str],
        cutoff_at: AwareDatetime,
    ) -> FullMemoryContextPack:
        excluded: Counter[str] = Counter()
        included: list[FullMemoryRevision] = []
        query_terms = self._tokens(query)
        current_heads = frozenset(self._ledger.read_heads(project_id).values())
        for item in self._store.list_revisions(project_id):
            reason: str | None = None
            if item.project_id != project_id:
                reason = "PROJECT_MISMATCH"
            elif self._resource_access is not None and not self._resource_access.may_read(item):
                reason = "RESOURCE_ACCESS_DENIED"
            elif item.transition != MemoryTransition.COMMIT:
                reason = f"TRANSITION_{item.transition.value}"
            elif item.support_status != "SUPPORTED":
                reason = "AMBIGUOUS_OR_CONFLICTING"
            elif item.authority_status != "AUTHORITATIVE":
                reason = "AUTHORITY_INVALID"
            elif not item.cutoff_valid or item.cutoff_at != cutoff_at:
                reason = "CUTOFF_INVALID"
            elif item.owner_revision_ref not in current_heads:
                reason = "OWNER_REVISION_NOT_CURRENT"
            elif (
                ResearchFreshnessService(self._ledger)
                .owner_eligibility(project_id, item.owner_revision_ref)
                .state
                != "CURRENT"
            ):
                reason = "DEPENDENCY_REVIEW_REQUIRED"
            elif not item.recall_eligible:
                reason = "RECALL_INELIGIBLE"
            elif target_use == "ACTION_CONTEXT" and not item.action_eligible:
                reason = "ACTION_INELIGIBLE"
            elif not self._scope_matches(item.scope, scope):
                reason = "SCOPE_MISMATCH"
            elif query_terms and not query_terms.intersection(item.query_terms):
                reason = "QUERY_IRRELEVANT"
            if reason is None:
                included.append(item)
            else:
                excluded[reason] += 1
        if self._reranker is not None and included:
            order = self._reranker.rank(
                query,
                tuple((item.memory_revision_id, item.content_excerpt) for item in included),
            )
            by_id = {item.memory_revision_id: item for item in included}
            included = [by_id[item] for item in order if item in by_id]
        now = self._clock.now()
        query_draft = {
            "project_id": project_id,
            "thread_id": thread_id,
            "query": query,
            "target_use": target_use,
            "scope": scope,
            "cutoff_at": cutoff_at,
            "included": tuple(item.revision_digest for item in included),
            "excluded": dict(sorted(excluded.items())),
        }
        context = FullMemoryContextPack(
            context_pack_id=self._ids.new("memory-context"),
            project_id=project_id,
            thread_id=thread_id,
            query=query,
            target_use=target_use,
            cutoff_at=cutoff_at,
            included=tuple(included),
            excluded_reason_counts=dict(sorted(excluded.items())),
            query_digest=domain_digest(
                "FULL_MEMORY_QUERY", "1.0.0", canonical_payload(query_draft)
            ),
            injected_into_thread=bool(included),
            created_at=now,
        )
        self._store.put_context(context)
        return context

    def rebuild_projections(self, project_id: str) -> tuple[MemoryProjection, ...]:
        revisions = self._store.list_revisions(project_id)
        projections = self.prepare_projections(project_id, revisions)
        if not projections:
            return ()
        with self._ledger.transaction():
            current = self._store.list_revisions(project_id)
            if tuple(sorted(item.revision_digest for item in current)) != tuple(
                sorted(item.revision_digest for item in revisions)
            ):
                raise ValueError("MEMORY_PROJECTION_SOURCE_STALE")
            self._store.replace_projections(project_id, projections)
        return projections

    def prepare_projections(
        self,
        project_id: str,
        revisions: tuple[FullMemoryRevision, ...],
        *,
        basis: MemoryPreparationBasis | None = None,
        staged_revisions: tuple[StagedRevision, ...] = (),
    ) -> tuple[MemoryProjection, ...]:
        if self._resource_access is not None and not self._resource_access.can_project(
            project_id, revisions, staged_revisions
        ):
            return ()
        source_digests = tuple(item.revision_digest for item in revisions)
        vectors: list[tuple[str, tuple[int, ...]]] = []
        if self._embedding is not None:
            for item in revisions:
                if basis is not None:
                    self.require_current(basis)
                vectors.append(
                    (item.memory_revision_id, self._embedding.embed(item.content_excerpt))
                )
        if basis is not None:
            self.require_current(basis)
        relations = (
            {}
            if self._projection_builder is None
            else self._projection_builder.build(
                tuple(
                    (
                        item.memory_revision_id,
                        item.owner_revision_ref,
                        item.source_ref or "SOURCE:UNKNOWN",
                    )
                    for item in revisions
                )
            )
        )
        checkpoint = domain_digest(
            "MEMORY_PROJECTION_CHECKPOINT",
            "1.0.0",
            canonical_payload({"source_revision_digests": source_digests}),
        )
        now = self._clock.now()
        projections = tuple(
            MemoryProjection(
                projection_id=f"memory-projection:{project_id}:{kind.lower()}",
                project_id=project_id,
                projection_type=kind,
                source_revision_digests=source_digests,
                payload_digest=domain_digest(
                    f"MEMORY_{kind}_PROJECTION",
                    "1.0.0",
                    canonical_payload(
                        {
                            "source_revision_digests": source_digests,
                            "derived": True,
                            "semantic_payload": (
                                vectors
                                if kind == "VECTOR"
                                else relations
                                if kind == "GRAPH"
                                else (),
                            ),
                        }
                    ),
                ),
                rebuild_checkpoint=checkpoint,
                created_at=now,
            )
            for kind in ("KEYWORD", "VECTOR", "GRAPH", "SUMMARY")
        )
        return projections

    def _has_conflict(
        self,
        *,
        revisions: tuple[FullMemoryRevision, ...],
        kind: MemoryKind,
        query_terms: frozenset[str],
        content_excerpt: str,
    ) -> bool:
        for item in revisions:
            if item.transition != MemoryTransition.COMMIT or item.kind != kind:
                continue
            if (
                query_terms.intersection(item.query_terms)
                and item.content_excerpt != content_excerpt
            ):
                return True
        return False

    @staticmethod
    def _content_excerpt(source_ref: str | None, assertion: str | None, snapshot: object) -> str:
        parts = [source_ref or "", assertion or ""]
        if assertion is None and snapshot is not None:
            content = getattr(snapshot, "content", {})
            parts.append(canonical_payload(content).decode())
        return "\n".join(part for part in parts if part)[:8_000]

    @staticmethod
    def _tokens(value: str) -> frozenset[str]:
        return frozenset(match.group(0).casefold() for match in _TOKEN.finditer(value))

    @staticmethod
    def _scope_matches(left: dict[str, str], right: dict[str, str]) -> bool:
        # Origin workstream is provenance under B; scientific applicability stays scoped.
        return all(right.get(key) == value for key, value in left.items() if key != "workstream")

    @staticmethod
    def _reduce(reviews: tuple[MemoryRoleReview, ...]) -> MemoryTransition:
        verdicts = {review.verdict for review in reviews}
        if MemoryReviewVerdict.QUARANTINE in verdicts:
            return MemoryTransition.QUARANTINE
        if MemoryReviewVerdict.HOLD in verdicts:
            return MemoryTransition.HOLD
        if MemoryReviewVerdict.REVISE in verdicts:
            return MemoryTransition.REVISE
        return MemoryTransition.COMMIT
