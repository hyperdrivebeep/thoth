from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.adapters.storage import (
    SqliteActionStore,
    SqliteAtomicUnitOfWork,
    SqliteLedger,
    migrate_sqlite_database,
)
from thoth.application.services import RevisionCommitService
from thoth.domain.action_full import ActionAuditRecord, ActionRecord
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, tzinfo=UTC)


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}:{self.value}"


def test_action_semantic_uow_rolls_back_revision_projection_audit_and_receipt(
    tmp_path: Path,
) -> None:
    migrate_sqlite_database(tmp_path / "thoth.sqlite3")
    ledger = SqliteLedger(tmp_path / "thoth.sqlite3")
    ledger.initialize()
    store = SqliteActionStore(ledger.engine)
    uow = SqliteAtomicUnitOfWork(ledger.engine)
    clock = FixedClock()
    ids = SequenceIds()
    record = ActionRecord(
        action_revision_id="action-revision:atomic",
        action_id="action:atomic",
        project_id="project:atomic",
        object_id="object:atomic",
        portfolio_id="portfolio:atomic",
        primary_purpose="ANALYSIS_COMPUTATION",
        specification={"description": "atomic action"},
        evidence_refs=("span:atomic",),
        expected_observation_or_change={"description": "bounded result"},
        effect_vector={"effect_completeness_confirmed": True},
        impact_set={},
        risk_tier="R0",
        required_processes=(),
        required_roles=(),
        revision_digest="a" * 64,
        created_at=clock.now(),
    )
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id="snapshot:atomic",
        project_id=record.project_id,
        entity_type=EntityType.ACTION,
        entity_id=record.action_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision_draft = {
        "revision_id": "revision:atomic",
        "project_id": record.project_id,
        "entity_type": EntityType.ACTION,
        "entity_id": record.action_id,
        "snapshot_id": snapshot.snapshot_id,
        "parent_revision_digests": (),
        "actor": ActorRef(actor_id="agent:test", kind=ActorKind.AGENT, role="test"),
        "reason": "atomic rollback test",
        "evidence_refs": record.evidence_refs,
        "affected_refs": (),
        "created_at": clock.now(),
        "schema_version": "1.0.0",
    }
    revision = SemanticRevision.model_validate(
        {
            **revision_draft,
            "revision_digest": domain_digest(
                "SEMANTIC_REVISION", "1.0.0", canonical_payload(revision_draft)
            ),
        }
    )
    audit_draft = {
        "project_id": record.project_id,
        "subject_id": record.action_id,
        "event_type": "action/test",
        "payload": {"revision": record.revision_digest},
        "created_at": clock.now(),
    }
    audit = ActionAuditRecord(
        audit_id="audit:atomic",
        project_id=record.project_id,
        subject_id=record.action_id,
        event_type="action/test",
        payload={"revision": record.revision_digest},
        event_digest=domain_digest("ACTION_AUDIT", "1.0.0", canonical_payload(audit_draft)),
        created_at=clock.now(),
    )

    with pytest.raises(RuntimeError, match="injected atomic fault"), uow.transaction():
        RevisionCommitService(
            ledger,
            clock,
            ids,
            policy_version="policy:atomic",
        ).commit(
            RevisionChangeSet(
                changeset_id="changeset:atomic",
                project_id=record.project_id,
                expected_heads={},
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(),
                actor=revision.actor,
                reason=revision.reason,
            )
        )
        store.add_action(record)
        store.append_audit(audit)
        raise RuntimeError("injected atomic fault")

    assert ledger.read_heads(record.project_id) == {}
    assert ledger.read_receipts(record.project_id) == ()
    assert store.list_actions(record.project_id) == ()
    assert store.list_audit(record.project_id, record.action_id) == ()
    ledger.close()
