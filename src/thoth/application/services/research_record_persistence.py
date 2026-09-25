"""Explicit research snapshot codecs and canonical staging shared by normal/public paths."""

from __future__ import annotations

from thoth.domain.action_full import (
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
)
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.research_identity import ResearchIdentityError
from thoth.domain.research_identity import decode_research as decode_research
from thoth.domain.revision import EntitySnapshot, SemanticRevision, StagedRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_identity import ResearchIdentityStorePort
from thoth.ports.runtime import IdGeneratorPort


def index_revision(
    ledger: LedgerPort, index: ResearchIdentityStorePort, project_id: str, digest: str
) -> None:
    revision = ledger.read_revision_by_digest(project_id, digest)
    snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
    if revision is None or snapshot is None:
        raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
    index.add(decode_research(revision, snapshot).identity)


def rebuild_identity_index(ledger: LedgerPort, index: ResearchIdentityStorePort) -> None:
    pending = index.unindexed_revisions()
    with ledger.transaction():
        for revision, snapshot in pending:
            index.add(decode_research(revision, snapshot).identity)


def stage_full_record(
    record: HypothesisRecord
    | HypothesisPortfolioRecord
    | ActionRecord
    | ActionPortfolioRecord
    | ActionPlanRecord,
    *,
    ids: IdGeneratorPort,
    actor: ActorRef,
    reason: str,
) -> StagedRevision:
    if isinstance(record, HypothesisRecord):
        entity_type, identifier, revision_id = (
            EntityType.HYPOTHESIS,
            record.hypothesis_id,
            record.hypothesis_revision_id,
        )
    elif isinstance(record, HypothesisPortfolioRecord):
        entity_type, identifier, revision_id = (
            EntityType.HYPOTHESIS,
            record.portfolio_id,
            record.portfolio_revision_id,
        )
    elif isinstance(record, ActionRecord):
        entity_type, identifier, revision_id = (
            EntityType.ACTION,
            record.action_id,
            record.action_revision_id,
        )
    elif isinstance(record, ActionPortfolioRecord):
        entity_type, identifier, revision_id = (
            EntityType.ACTION,
            record.portfolio_id,
            record.portfolio_revision_id,
        )
    else:
        entity_type, identifier, revision_id = (
            EntityType.ACTION,
            record.plan_id,
            record.plan_revision_id,
        )
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=record.project_id,
        entity_type=entity_type,
        entity_id=identifier,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id=revision_id,
        project_id=record.project_id,
        entity_type=entity_type,
        entity_id=identifier,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=()
        if record.supersedes_revision_digest is None
        else (record.supersedes_revision_digest,),
        actor=actor,
        reason=reason,
        evidence_refs=tuple(getattr(record, "evidence_refs", ())),
        affected_refs=(),
        revision_digest=record.revision_digest,
        created_at=record.created_at,
    )
    return StagedRevision(snapshot=snapshot, revision=revision)
