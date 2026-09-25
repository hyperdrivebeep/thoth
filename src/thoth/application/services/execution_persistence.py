"""Atomic execution revision, projection and audit persistence."""

from __future__ import annotations

from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.execution_full import ExecutionAuditRecord, PlanExecutionRecord
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def append_execution_audit(
    project_id: str,
    execution_id: str,
    event_type: str,
    payload: dict[str, object],
    *,
    store: ExecutionStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> ExecutionAuditRecord:
    draft: dict[str, object] = {
        "project_id": project_id,
        "plan_execution_id": execution_id,
        "event_type": event_type,
        "payload": payload,
        "created_at": clock.now(),
    }
    record = ExecutionAuditRecord.model_validate(
        {
            **draft,
            "audit_id": ids.new("execution-audit"),
            "event_digest": domain_digest("EXECUTION_AUDIT", "1.0.0", canonical_payload(draft)),
        }
    )
    store.append_audit(record)
    return record


def persist_execution(
    record: PlanExecutionRecord,
    event_type: str,
    *,
    store: ExecutionStorePort,
    ledger: LedgerPort,
    commits: RevisionCommitService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> tuple[PlanExecutionRecord, CommitResult]:
    with ledger.transaction():
        content = record.model_dump(mode="python")
        snapshot = EntitySnapshot(
            snapshot_id=ids.new("snapshot"),
            project_id=record.project_id,
            entity_type=EntityType.EXECUTION,
            entity_id=record.plan_execution_id,
            schema_version="1.0.0",
            content=content,
            content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        actor = ActorRef(
            actor_id="agent:execution-coordinator",
            kind=ActorKind.AGENT,
            role="trusted-coordinator",
        )
        revision = SemanticRevision(
            revision_id=record.execution_revision_id,
            project_id=record.project_id,
            entity_type=EntityType.EXECUTION,
            entity_id=record.plan_execution_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=(
                ()
                if record.supersedes_revision_digest is None
                else (record.supersedes_revision_digest,)
            ),
            actor=actor,
            reason=event_type,
            evidence_refs=record.observation_refs,
            affected_refs=record.effect_refs,
            revision_digest=record.revision_digest,
            created_at=record.updated_at,
        )
        expected = (
            {}
            if record.supersedes_revision_digest is None
            else {f"EXECUTION:{record.plan_execution_id}": record.supersedes_revision_digest}
        )
        commit = commits.commit(
            RevisionChangeSet(
                changeset_id=ids.new("changeset"),
                project_id=record.project_id,
                expected_heads=expected,
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(),
                actor=actor,
                reason=event_type,
            )
        )
        if not commit.committed_revision_ids:
            raise ValueError("Execution commit branched because expected revision changed")
        store.add_execution(record)
        append_execution_audit(
            record.project_id,
            record.plan_execution_id,
            event_type,
            {"revision_digest": record.revision_digest},
            store=store,
            clock=clock,
            ids=ids,
        )
        return record, commit
