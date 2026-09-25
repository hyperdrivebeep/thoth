"""Typed canonical snapshots and execution journals on existing transactional owners.

No parallel research truth table: control records hold only execution journals;
request/result/review content uses snapshots, revisions, heads and commit receipts.
"""

from pydantic import BaseModel

from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.research_codec import decode_research_record
from thoth.domain.research_request import RevisionRef
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.journal import JournalPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class RequestRecords:
    def __init__(
        self,
        ledger: LedgerPort,
        controls: ControlRecordStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        events: JournalPort | None = None,
    ) -> None:
        self.ledger, self.controls, self.clock, self.ids = ledger, controls, clock, ids
        self.events = events
        self.journals = ControlRecordService(store=controls, clock=clock, ids=ids)

    def read(
        self, project_id: str, entity_type: EntityType, entity_id: str
    ) -> tuple[RevisionRef, dict[str, object]] | None:
        digest = self.ledger.read_heads(project_id).get(f"{entity_type.value}:{entity_id}")
        if digest is None:
            return None
        revision = self.ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else self.ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise ValueError("REQUEST_REVISION_MISSING")
        decode_research_record(dict(snapshot.content))
        return (
            RevisionRef(
                project_id=project_id,
                entity_type=entity_type.value,
                entity_id=entity_id,
                revision_id=revision.revision_id,
                revision_digest=digest,
            ),
            dict(snapshot.content),
        )

    def save(
        self,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        record: BaseModel,
        actor_id: str = "agent:research",
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> RevisionRef:
        with self.ledger.transaction():
            ref, staged = self.stage(
                project_id, entity_type, entity_id, record, actor_id, evidence_refs=evidence_refs
            )
            self.commit_staged((staged,))
            return ref

    def stage(
        self,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        record: BaseModel,
        actor_id: str = "agent:research",
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[RevisionRef, StagedRevision]:
        content = record.model_dump(mode="python")
        decode_research_record(content)
        heads = dict(self.ledger.read_heads(project_id))
        key = f"{entity_type.value}:{entity_id}"
        parent = heads.get(key)
        principal = current_authenticated_actor()
        actor = ActorRef(
            actor_id=actor_id,
            kind=ActorKind.HUMAN
            if actor_id == "human:local-user"
            or (principal is not None and principal.actor_id == actor_id)
            else ActorKind.AGENT,
            role="research-record-writer",
            project_id=project_id,
        )
        snapshot = EntitySnapshot(
            snapshot_id=self.ids.new("snapshot"),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            schema_version="2.0.0",
            content=content,
            content_digest=domain_digest("SNAPSHOT", "2.0.0", canonical_payload(content)),
        )
        payload = dict(
            revision_id=self.ids.new("revision"),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=() if parent is None else (parent,),
            actor=actor,
            reason=f"{record.__class__.__name__} publication",
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
            affected_refs=(),
            created_at=self.clock.now(),
            schema_version="2.0.0",
        )
        revision = SemanticRevision.model_validate(
            {
                **payload,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION", "2.0.0", canonical_payload(payload)
                ),
            }
        )
        return RevisionRef(
            project_id=project_id,
            entity_type=entity_type.value,
            entity_id=entity_id,
            revision_id=revision.revision_id,
            revision_digest=revision.revision_digest,
        ), StagedRevision(snapshot=snapshot, revision=revision)

    def commit_staged(self, staged: tuple[StagedRevision, ...]) -> None:
        if not staged:
            return
        project = staged[0].revision.project_id
        keys = [f"{s.revision.entity_type.value}:{s.revision.entity_id}" for s in staged]
        if len(keys) != len(set(keys)) or any(s.revision.project_id != project for s in staged):
            raise ValueError("PUBLICATION_RECORD_SCOPE_OR_DUPLICATE")
        with self.ledger.transaction():
            heads = self.ledger.read_heads(project)
            for key, item in zip(keys, staged, strict=True):
                expected = item.revision.parent_revision_digests
                if heads.get(key) != (expected[-1] if expected else None):
                    raise ValueError("PUBLICATION_RECORD_HEAD_CHANGED")
            committed = RevisionCommitService(
                self.ledger, self.clock, self.ids, policy_version="research-request-v2"
            ).commit(
                RevisionChangeSet(
                    changeset_id=self.ids.new("changeset"),
                    project_id=project,
                    expected_heads={key: heads[key] for key in keys if key in heads},
                    staged_revisions=staged,
                    impact_plan=ImpactPropagationPlan(),
                    actor=staged[0].revision.actor,
                    reason="Atomic research checkpoint",
                )
            )
            if committed.disposition.value != "FAST_FORWARD":
                raise ValueError("REQUEST_PUBLICATION_CONFLICT")

    def journal(self, project: str, key: str, value: BaseModel, state: str = "RUNNING") -> None:
        self.journals.create(
            project_id=project,
            namespace="RESEARCH_EXECUTION",
            record_type=value.__class__.__name__,
            record_id=key,
            state=state,
            payload=value.model_dump(mode="python"),
        )

    def journal_read[T: BaseModel](self, project: str, key: str, codec: type[T]) -> T | None:
        record = self.controls.read(project, "RESEARCH_EXECUTION", key)
        return None if record is None else codec.model_validate(record.payload)
