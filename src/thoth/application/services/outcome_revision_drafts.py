"""Pure construction of the Outcome revision records and the ledger snapshot parts.

The id and clock arguments are the service's own port objects, called in the original order. The
service keeps the head expectation, the RevisionChangeSet (whose id is generated after the commit
callee is looked up) and the commit itself, so their relative order is untouched.
"""

from __future__ import annotations

from thoth.application.services.outcome_record_drafts import record_digest
from thoth.domain.actor import ActorRef
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.outcome_full import OutcomeAssessmentRecord, OutcomeSeriesRecord
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def revised_series_record(
    current: OutcomeSeriesRecord,
    updates: dict[str, object],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeSeriesRecord:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "series_revision_id": ids.new("outcome-series-revision"),
            "revision": current.revision + 1,
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return OutcomeSeriesRecord.model_validate(
        {**draft, "revision_digest": record_digest("OUTCOME_SERIES", draft)}
    )


def revised_assessment_record(
    current: OutcomeAssessmentRecord,
    updates: dict[str, object],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> OutcomeAssessmentRecord:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "assessment_revision_id": ids.new("outcome-assessment-revision"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return OutcomeAssessmentRecord.model_validate(
        {**draft, "revision_digest": record_digest("OUTCOME_ASSESSMENT", draft)}
    )


def commit_parts(
    record: OutcomeSeriesRecord | OutcomeAssessmentRecord,
    entity_id: str,
    revision_id: str,
    revision_digest: str,
    supersedes: str | None,
    event_type: str,
    evidence_refs: tuple[str, ...],
    *,
    ids: IdGeneratorPort,
) -> tuple[EntitySnapshot, ActorRef, SemanticRevision]:
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=record.project_id,
        entity_type=EntityType.OUTCOME,
        entity_id=entity_id,
        schema_version="1.0.0",
        content=content,
        content_digest=record_digest("ENTITY_SNAPSHOT", content),
    )
    actor = ActorRef(
        actor_id="agent:outcome-evaluator",
        kind=ActorKind.AGENT,
        role="outcome-evaluator",
    )
    revision = SemanticRevision(
        revision_id=revision_id,
        project_id=record.project_id,
        entity_type=EntityType.OUTCOME,
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
