"""Typed Hypothesis revision data; currentness guards and publication stay in the service."""

from __future__ import annotations

from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.runtime import IdGeneratorPort


def hypothesis_revision_records(
    record: HypothesisRecord | HypothesisPortfolioRecord,
    *,
    entity_id: str,
    revision_id: str,
    revision_digest: str,
    supersedes: str | None,
    event_type: str,
    evidence_refs: tuple[str, ...],
    ids: IdGeneratorPort,
) -> tuple[EntitySnapshot, ActorRef, SemanticRevision]:
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=entity_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    actor = ActorRef(
        actor_id="agent:hypothesis-service", kind=ActorKind.AGENT, role="hypothesis-editor"
    )
    revision = SemanticRevision(
        revision_id=revision_id,
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=entity_id,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=() if supersedes is None else (supersedes,),
        actor=actor,
        reason=event_type,
        evidence_refs=evidence_refs,
        affected_refs=(),
        revision_digest=revision_digest,
        created_at=record.created_at,
    )
    return snapshot, actor, revision
