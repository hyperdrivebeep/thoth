from __future__ import annotations

from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.errors import InvariantViolation
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class RestoreResult(DomainModel):
    selected_revision_id: str
    restored_revision_id: str
    stale_refs: tuple[str, ...]
    recalculate_refs: tuple[str, ...]
    commit: CommitResult


class RestoreService:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        dependencies: DependencyGraphPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._ledger = ledger
        self._dependencies = dependencies
        self._commits = commits
        self._clock = clock
        self._ids = ids

    def restore_as_new_revision(
        self,
        *,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        selected_historical_revision_id: str,
        expected_current_head: str,
        actor: ActorRef,
        reason: str,
    ) -> RestoreResult:
        revisions = self._ledger.read_revisions(project_id, entity_type.value, entity_id)
        selected = next(
            (
                revision
                for revision in revisions
                if revision.revision_id == selected_historical_revision_id
            ),
            None,
        )
        if selected is None:
            raise InvariantViolation("selected historical revision does not exist")
        snapshot = self._ledger.read_snapshot(selected.snapshot_id)
        if snapshot is None:
            raise InvariantViolation("selected revision snapshot does not exist")
        aggregate_key = f"{entity_type.value}:{entity_id}"
        current_head = self._ledger.read_heads(project_id).get(aggregate_key)
        if current_head is None:
            raise InvariantViolation("target entity has no current head")
        if current_head != expected_current_head:
            raise InvariantViolation("restore expected head is stale")

        new_snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            schema_version=snapshot.schema_version,
            content=snapshot.content,
            content_digest=snapshot.content_digest,
        )
        revision_id = self._ids.new("revision")
        created_at = self._clock.now()
        revision_payload: dict[str, object] = {
            "revision_id": revision_id,
            "project_id": project_id,
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "snapshot_id": new_snapshot.snapshot_id,
            "parent_revision_digests": [current_head],
            "actor": actor,
            "reason": reason,
            "evidence_refs": list(selected.evidence_refs),
            "affected_refs": [f"restored-from:{selected.revision_id}"],
            "created_at": created_at,
            "schema_version": "1.0.0",
        }
        new_revision = SemanticRevision.model_validate(
            {
                **revision_payload,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION",
                    "1.0.0",
                    canonical_payload(revision_payload),
                ),
            }
        )
        stale_refs = self._dependencies.downstream(project_id, aggregate_key)
        changeset = RevisionChangeSet(
            changeset_id=self._ids.new("changeset"),
            project_id=project_id,
            expected_heads={aggregate_key: expected_current_head},
            staged_revisions=(StagedRevision(snapshot=new_snapshot, revision=new_revision),),
            impact_plan=ImpactPropagationPlan(recalculate_refs=stale_refs),
            actor=actor,
            reason=reason,
        )
        commit = self._commits.commit(changeset)
        return RestoreResult(
            selected_revision_id=selected.revision_id,
            restored_revision_id=new_revision.revision_id,
            stale_refs=stale_refs,
            recalculate_refs=stale_refs,
            commit=commit,
        )
