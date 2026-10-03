"""A user's correction of one stored memory, stored as a single atomic unit.

The correction is a human-authored ledger record plus a new memory version that stands on the
same record version as the memory it corrects, and that the ordinary review path judges. The
record and the memory are committed together or not at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.memory_safety import REDACTED_MEMORY, memory_text_is_unsafe
from thoth.application.services.memory_supersession import (
    latest_memory_digest,
    superseded_memory_digests,
)
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.revision_service import CommitDisposition, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import (
    ActorKind,
    EntityType,
    MemoryKind,
    MemoryPayloadMode,
    RecallEligibility,
)
from thoth.domain.memory import FullMemoryRevision, MemoryRecord, MemoryTransition
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import FullMemoryStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

EDIT_SCHEMA = "memory-edit.1.0.0"
CORRECTION_PREFIX = "MEMORY:"


@dataclass(frozen=True)
class MemoryEditResult:
    edit_id: str
    revision: FullMemoryRevision
    target_revision_digest: str


class MemoryEditService:
    def __init__(
        self,
        *,
        memory: FullProjectMemoryService,
        full: FullMemoryStorePort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._memory, self._full, self._ledger = memory, full, ledger
        self._commits, self._clock, self._ids = commits, clock, ids

    async def propose(
        self,
        *,
        project_id: str,
        target_revision_digest: str,
        corrected_text: str,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> MemoryEditResult:
        target = self._resolve_target(project_id, target_revision_digest)
        edit_id = self._ids.new("memory-edit")
        basis = self._memory.capture_basis(
            project_id=project_id, cutoff_at=target.cutoff_at, scope=dict(target.scope)
        )
        staged = self._stage(project_id, edit_id, target, corrected_text, reason, evidence_refs)
        candidate = self._candidate(project_id, edit_id, target, corrected_text)
        prepared = await self._memory.prepare_thread_results(
            basis=basis,
            thread_id=edit_id,
            candidates=(candidate,),
            staged_revisions=(staged,),
            parent_by_memory_id={candidate.memory_id: target.revision_digest},
        )
        actor = staged.revision.actor
        with self._ledger.transaction():
            if self._resolve_target(project_id, target_revision_digest) != target:
                raise RpcApplicationError(RpcErrorCode.STALE_CHECKPOINT, "MEMORY_EDIT_HEAD_CHANGED")
            commit = self._commits.commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=project_id,
                    expected_heads={},
                    expected_head_set_digest=basis.head_set_digest,
                    staged_revisions=(staged,),
                    impact_plan=ImpactPropagationPlan(),
                    actor=actor,
                    reason=f"user memory correction {edit_id}",
                )
            )
            if commit.disposition != CommitDisposition.FAST_FORWARD:
                raise RpcApplicationError(RpcErrorCode.STALE_CHECKPOINT, "MEMORY_EDIT_HEAD_CHANGED")
            self._memory.commit_prepared(prepared, expected_head=commit.after_head_set_digest)
        (revision,) = prepared.revisions
        return MemoryEditResult(edit_id, revision, target.revision_digest)

    def _resolve_target(self, project_id: str, digest: str) -> FullMemoryRevision:
        """The version a correction replaces: a correction that was not accepted points back."""

        stored = self._full.list_revisions(project_id)
        by_digest = {item.revision_digest: item for item in stored}
        replaced = superseded_memory_digests(stored)
        target = by_digest.get(digest)
        if target is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "MEMORY_EDIT_TARGET_NOT_FOUND")
        self._require_not_replaced(stored, replaced, digest)
        while (
            target.transition != MemoryTransition.COMMIT
            and (target.source_ref or "").startswith(CORRECTION_PREFIX)
            and target.parent_revision_digest in by_digest
        ):
            target = by_digest[target.parent_revision_digest or ""]
        self._require_not_replaced(stored, replaced, target.revision_digest)
        self._require_current_basis(project_id, target)
        return target

    def _require_current_basis(self, project_id: str, target: FullMemoryRevision) -> None:
        """Same two checks, in the same order, as recall and the memory list."""

        heads = frozenset(self._ledger.read_heads(project_id).values())
        stale = target.owner_revision_ref not in heads or (
            ResearchFreshnessService(self._ledger)
            .owner_eligibility(project_id, target.owner_revision_ref)
            .state
            != "CURRENT"
        )
        if stale:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "MEMORY_EDIT_TARGET_STALE")

    @staticmethod
    def _require_not_replaced(
        stored: Sequence[FullMemoryRevision], replaced: frozenset[str], digest: str
    ) -> None:
        if digest in replaced:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "MEMORY_EDIT_TARGET_SUPERSEDED",
                data={"latest_revision_digest": latest_memory_digest(stored, digest)},
            )

    def _stage(
        self,
        project_id: str,
        edit_id: str,
        target: FullMemoryRevision,
        corrected_text: str,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> StagedRevision:
        content: dict[str, object] = {
            "target_memory_id": target.memory_id,
            "target_revision_digest": target.revision_digest,
            "corrected_text": corrected_text,
            "reason": reason,
            "evidence_refs": list(evidence_refs),
        }
        for field, text in (("corrected_text", corrected_text), ("reason", reason)):
            if memory_text_is_unsafe(text):
                # The ledger never forgets, so text the review would quarantine is kept as a digest.
                content[field] = REDACTED_MEMORY
                content[f"{field}_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=project_id,
            entity_type=EntityType.MEMORY,
            entity_id=edit_id,
            schema_version=EDIT_SCHEMA,
            content=content,
            content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        draft: dict[str, object] = {
            "revision_id": self._ids.new("revision"),
            "project_id": project_id,
            "entity_type": EntityType.MEMORY.value,
            "entity_id": edit_id,
            "snapshot_id": snapshot.snapshot_id,
            "parent_revision_digests": [],
            "actor": self._actor(project_id),
            "reason": "USER_MEMORY_CORRECTION",
            "evidence_refs": list(evidence_refs),
            "affected_refs": [],
            "created_at": self._clock.now(),
            "schema_version": EDIT_SCHEMA,
        }
        revision = SemanticRevision.model_validate(
            {
                **draft,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION", EDIT_SCHEMA, canonical_payload(draft)
                ),
            }
        )
        return StagedRevision(snapshot=snapshot, revision=revision)

    def _candidate(
        self, project_id: str, edit_id: str, target: FullMemoryRevision, corrected_text: str
    ) -> MemoryRecord:
        draft: dict[str, object] = {
            "memory_id": self._ids.new("memory"),
            "project_id": project_id,
            "payload_mode": MemoryPayloadMode.MEMORY_ASSERTION.value,
            "kind": MemoryKind.LESSON.value,
            # The correction stands on the same record version as the memory it corrects.
            "owner_revision_ref": target.owner_revision_ref,
            "source_ref": f"MEMORY:{edit_id}",
            "assertion": REDACTED_MEMORY
            if memory_text_is_unsafe(corrected_text)
            else corrected_text,
            "recall_eligibility": RecallEligibility.WORKING_CONTEXT.value,
        }
        return MemoryRecord.model_validate(
            {
                **draft,
                "revision_digest": domain_digest(
                    "MEMORY_EDIT_CANDIDATE", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    @staticmethod
    def _actor(project_id: str) -> ActorRef:
        session = current_authenticated_actor()
        return ActorRef(
            actor_id="human:local-operator" if session is None else session.actor_id,
            kind=ActorKind.HUMAN,
            role="memory-editor" if session is None else session.role,
            project_id=project_id,
            session_id=None if session is None else session.session_id,
            role_assignment_ref=None if session is None else session.role_assignment_id,
        )
