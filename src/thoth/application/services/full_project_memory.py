from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Literal

from pydantic import AwareDatetime

from thoth.application.services.memory_admission import MemoryAdmissionService
from thoth.application.services.memory_recall import (
    AUTO_MEMORY_NO_EVIDENCE,
    OMITTED_BY_BUDGET,
    RecallLimits,
    auto_memory_lacks_evidence,
    lacks_evidence,
    narrow_recall,
    plan_recall,
)
from thoth.application.services.memory_relation import MemorySubject
from thoth.application.services.memory_relation_resolver import (
    MemoryRelationBudget,
    MemoryRelationResolver,
)
from thoth.application.services.memory_review_service import MemoryReviewService
from thoth.application.services.memory_revision_seal import (
    seal_receipt,
    seal_revision,
    support_status,
)
from thoth.application.services.memory_safety import REDACTED_MEMORY, memory_text_is_unsafe
from thoth.application.services.memory_shape import memory_body
from thoth.application.services.memory_summary import memory_is_container
from thoth.application.services.memory_supersession import superseded_memory_digests
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.scoped_memory import MemoryResourceAccess
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemorySelectionRecord,
    MemoryTransition,
    MemoryTransitionReceipt,
)
from thoth.domain.memory_preparation import (
    FullMemoryPromotionResult,
    MemoryPreparationBasis,
    MemoryPreparationHeadChanged,
    MemoryPreparationStale,
    PreparedMemoryPromotion,
)
from thoth.domain.memory_relation import MemoryRelationJudgment
from thoth.domain.revision import StagedRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import (
    FullMemoryStorePort,
    MemoryEmbeddingPort,
    MemoryInjectionPort,
    MemoryProjectionBuilderPort,
    MemoryRelationJudgePort,
    MemoryRerankerPort,
    MemoryReviewerPort,
    MemoryStorePort,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_TOKEN = re.compile(r"[0-9A-Za-z가-힣_]{3,}")
_UNKNOWN_SHAPE_CHARS = 640


def _hold_for_missing_evidence(
    reviews: tuple[MemoryRoleReview, ...],
) -> tuple[MemoryRoleReview, ...]:
    """The facts role holds an automatic memory with no source behind it; still four reviews."""

    return tuple(
        review.model_copy(
            update={"verdict": MemoryReviewVerdict.HOLD, "reason_code": AUTO_MEMORY_NO_EVIDENCE}
        )
        if review.role == MemoryReviewRole.FACTS
        else review
        for review in reviews
    )


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
        injection: MemoryInjectionPort | None = None,
        relation_judge: MemoryRelationJudgePort | None = None,
        limits: RecallLimits | None = None,
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
        self._injection = injection
        self._relations = MemoryRelationResolver(ledger, relation_judge)
        self._limits = limits or RecallLimits()

    @property
    def store(self) -> FullMemoryStorePort:
        """The stored memory versions, for read-only currentness checks."""

        return self._store

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

    def rebase_basis(
        self,
        basis: MemoryPreparationBasis,
        *,
        head_scope: Mapping[str, str],
        head_absent: tuple[str, ...] = (),
    ) -> MemoryPreparationBasis:
        """The basis as of now, narrowed to the heads the caller read; authority is kept.

        Memory added since the basis was first taken is then part of what the review sees, and an
        unrelated head no longer stops the preparation. A change in authority still does.
        """

        fresh = self.capture_basis(
            project_id=basis.project_id,
            cutoff_at=basis.cutoff_at,
            scope=dict(basis.scope),
            actor=basis.actor,
        )
        return fresh.model_copy(
            update={
                "authority": basis.authority,
                "head_scope": tuple(sorted(head_scope.items())),
                "head_absent": tuple(sorted(head_absent)),
            }
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
        if check_heads:
            if basis.head_scope is not None and expected_head is None:
                heads = self._ledger.read_heads(basis.project_id)
                if any(heads.get(key) != digest for key, digest in basis.head_scope) or any(
                    key in heads for key in basis.head_absent
                ):
                    raise MemoryPreparationHeadChanged("MEMORY_HEAD_CHANGED")
            elif current.head_set_digest != (expected_head or basis.head_set_digest):
                raise MemoryPreparationHeadChanged("MEMORY_HEAD_CHANGED")
        expected = basis.model_copy(
            update={
                "head_set_digest": current.head_set_digest,
                "head_scope": current.head_scope,
                "head_absent": current.head_absent,
            }
        )
        if current != expected:
            raise MemoryPreparationStale("MEMORY_PREPARATION_STALE")

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
        parent_by_memory_id: Mapping[str, str] | None = None,
        relation_budget: MemoryRelationBudget | None = None,
    ) -> PreparedMemoryPromotion:
        self.require_current(basis)
        project_id, cutoff_at, scope = basis.project_id, basis.cutoff_at, dict(basis.scope)
        staged_by_digest = {item.revision.revision_digest: item for item in staged_revisions}
        existing_revisions = self._store.list_revisions(project_id)
        # A version being replaced never counts as a conflicting other memory.
        replaced = superseded_memory_digests(existing_revisions) | frozenset(
            (parent_by_memory_id or {}).values()
        )
        visible_existing = tuple(
            r
            for r in existing_revisions
            if r.revision_digest not in replaced
            and (self._resource_access is None or self._resource_access.may_read(r))
        )
        readable_existing = tuple(
            r
            for r in existing_revisions
            if self._resource_access is None or self._resource_access.may_read(r)
        )
        existing_by_memory = {item.memory_id: item for item in existing_revisions}
        relation_budget = relation_budget or MemoryRelationBudget()
        staged_parents = {
            item.revision.revision_digest: item.revision.parent_revision_digests
            for item in staged_revisions
        }
        judgments: list[MemoryRelationJudgment] = []
        subjects: dict[str, MemorySubject] = {}
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
            content = None if snapshot is None else snapshot.content
            body = (
                None if candidate.assertion is not None or content is None else memory_body(content)
            )
            # A free-text memory is judged by its own words, never by ids in its source reference.
            if candidate.assertion is not None:
                words = candidate.assertion
            else:
                words = content_excerpt if body is None else body[1]
            query_terms = tuple(sorted(self._tokens(words)))
            unsafe = (
                memory_text_is_unsafe(content_excerpt) or candidate.assertion == REDACTED_MEMORY
            )
            owner_valid = owner is not None and owner.project_id == project_id
            content_reusable = len(query_terms) >= 2
            verdict = await self._relations.resolve_candidate(
                candidate,
                content=content,
                text=candidate.assertion or (content_excerpt if body is None else body[0]),
                scope=scope,
                staged_parents=staged_parents,
                parent_revision_digest=(parent_by_memory_id or {}).get(candidate.memory_id),
                existing=(*visible_existing, *revisions),
                readable=(*readable_existing, *revisions),
                budget=relation_budget,
                subjects=subjects,
            )
            judgments.extend(verdict.judgments)
            conflict = verdict.conflict
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
            support = support_status(
                owner_valid=owner_valid,
                contradiction=verdict.contradiction,
                ambiguous=verdict.ambiguous,
            )
            if (
                owner is not None
                and owner_valid
                and auto_memory_lacks_evidence(
                    candidate.payload_mode, candidate.kind, owner.evidence_refs
                )
            ):
                # No source span is behind this automatic memory: it is held, not remembered.
                reviews = _hold_for_missing_evidence(reviews)
                support = "NO_EVIDENCE"
            transition = self._reduce(reviews)
            now = self._clock.now()
            revision = seal_revision(
                revision_id=self._ids.new("memory-revision"),
                candidate=candidate,
                thread_id=thread_id,
                owner=owner,
                content_excerpt=content_excerpt,
                query_terms=query_terms,
                unsafe=unsafe,
                owner_valid=owner_valid,
                support=support,
                reviews=reviews,
                transition=transition,
                cutoff_at=cutoff_at,
                scope=scope,
                parent_revision_digest=(parent_by_memory_id or {}).get(candidate.memory_id),
                created_at=now,
            )
            receipt = seal_receipt(
                receipt_id=self._ids.new("memory-receipt"),
                revision=revision,
                reviews=reviews,
                recorded_at=now,
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
        result = self._promotion_result(revisions, receipts, projections, judgments)

        return PreparedMemoryPromotion(
            basis=basis,
            candidates=candidates,
            revisions=new_revisions,
            receipts=tuple(receipts),
            projections=projections,
            result=result,
        )

    @staticmethod
    def _promotion_result(
        revisions: list[FullMemoryRevision],
        receipts: list[MemoryTransitionReceipt],
        projections: tuple[MemoryProjection, ...],
        judgments: list[MemoryRelationJudgment],
    ) -> FullMemoryPromotionResult:
        """Project reviewed revisions into the public promotion result."""
        checkpoint = (
            projections[0].rebuild_checkpoint
            if projections
            else domain_digest(
                "MEMORY_PROJECTION_CHECKPOINT",
                "1.0.0",
                canonical_payload({"source_revision_digests": ()}),
            )
        )
        return FullMemoryPromotionResult(
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
            relation_judgments=tuple(judgments),
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
        excluded: dict[str, list[str]] = defaultdict(list)
        eligible: list[FullMemoryRevision] = []
        query_terms = self._tokens(query)
        stored = self._store.list_revisions(project_id)
        if self._injection is not None and not self._injection.enabled(project_id):
            excluded["MEMORY_INJECTION_OFF"] = [item.memory_revision_id for item in stored]
            return self._save_context(
                project_id, thread_id, query, target_use, scope, cutoff_at, (), excluded, None
            )
        current_heads = frozenset(self._ledger.read_heads(project_id).values())
        readable = {
            item.revision_digest: self._resource_access is None
            or self._resource_access.may_read(item)
            for item in stored
        }
        superseded = superseded_memory_digests(
            item for item in stored if readable[item.revision_digest]
        )
        for item in stored:
            reason: str | None = None
            if item.project_id != project_id:
                reason = "PROJECT_MISMATCH"
            elif not readable[item.revision_digest]:
                reason = "RESOURCE_ACCESS_DENIED"
            elif item.revision_digest in superseded:
                reason = "SUPERSEDED_BY_NEWER_VERSION"
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
            elif memory_is_container(item, self._ledger):
                # A bundle stored before bundles were left out: its members are recalled instead.
                reason = "CONTAINER_NOT_RECALLED"
            elif lacks_evidence(item):
                # An automatic hypothesis or action with no source span behind it; stored ones stay.
                reason = AUTO_MEMORY_NO_EVIDENCE
            elif target_use == "ACTION_CONTEXT" and not item.action_eligible:
                reason = "ACTION_INELIGIBLE"
            elif not self._scope_matches(item.scope, scope):
                reason = "SCOPE_MISMATCH"
            if reason is not None:
                excluded[reason].append(item.memory_revision_id)
                continue
            eligible.append(item)
        narrowing = narrow_recall(eligible, query, query_terms)
        relevant = narrowing.relevant
        for reason, ids in narrowing.excluded.items():
            excluded[reason].extend(ids)
        plan = plan_recall(
            relevant, query, query_terms, self._limits, follow_up=bool(narrowing.follow_up_markers)
        )
        excluded[OMITTED_BY_BUDGET] = [item.memory_revision_id for item in plan.omitted]
        selection = MemorySelectionRecord(
            limits=self._limits.as_dict(),
            eligible=tuple(item.memory_revision_id for item in eligible),
            retrieved=tuple(item.memory_revision_id for item in plan.retrieved),
            selected=tuple(item.memory_revision_id for item in plan.selected),
            context_included=tuple(item.memory_revision_id for item in plan.included),
            excluded={reason: tuple(ids) for reason, ids in sorted(excluded.items()) if ids},
            omitted_by_limit=dict(sorted(plan.omitted_by_limit.items())),
            truncated=tuple(plan.truncated),
            estimated_tokens=plan.tokens,
            common_terms=tuple(sorted(narrowing.common_terms)),
            follow_up=bool(narrowing.follow_up_markers),
            follow_up_markers=narrowing.follow_up_markers,
        )
        return self._save_context(
            project_id,
            thread_id,
            query,
            target_use,
            scope,
            cutoff_at,
            tuple(plan.included),
            excluded,
            selection,
        )

    def _save_context(
        self,
        project_id: str,
        thread_id: str,
        query: str,
        target_use: Literal["WORKING_CONTEXT", "ACTION_CONTEXT"],
        scope: dict[str, str],
        cutoff_at: AwareDatetime,
        included: tuple[FullMemoryRevision, ...],
        excluded: Mapping[str, list[str]],
        selection: MemorySelectionRecord | None,
    ) -> FullMemoryContextPack:
        counts = {reason: len(ids) for reason, ids in sorted(excluded.items()) if ids}
        if selection is None:
            selection = MemorySelectionRecord(
                injection_enabled=False,
                limits=self._limits.as_dict(),
                excluded={reason: tuple(ids) for reason, ids in sorted(excluded.items()) if ids},
            )
        query_draft = {
            "project_id": project_id,
            "thread_id": thread_id,
            "query": query,
            "target_use": target_use,
            "scope": scope,
            "cutoff_at": cutoff_at,
            "included": tuple(item.revision_digest for item in included),
            "excluded": counts,
        }
        context = FullMemoryContextPack(
            context_pack_id=self._ids.new("memory-context"),
            project_id=project_id,
            thread_id=thread_id,
            query=query,
            target_use=target_use,
            cutoff_at=cutoff_at,
            included=included,
            excluded_reason_counts=counts,
            query_digest=domain_digest(
                "FULL_MEMORY_QUERY", "1.0.0", canonical_payload(query_draft)
            ),
            injected_into_thread=bool(included),
            created_at=self._clock.now(),
            selection=selection,
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

    @staticmethod
    def _content_excerpt(source_ref: str | None, assertion: str | None, snapshot: object) -> str:
        """The stored text: the source, then one short line (a lesson's words, or the record's)."""

        parts = [source_ref or "", assertion or ""]
        if assertion is None and snapshot is not None:
            content = getattr(snapshot, "content", {})
            body = memory_body(content)
            if body is not None:
                parts.append(body[0])
            else:
                # An unfamiliar record: keep the start of it, within one recalled item's share.
                parts.append(canonical_payload(content).decode()[:_UNKNOWN_SHAPE_CHARS])
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
