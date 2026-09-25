from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.storage import (
    SqliteDependencyGraph,
    SqliteLedger,
    migrate_sqlite_database,
)
from thoth.application.services import RestoreService, RevisionCommitService
from thoth.application.services.revision_service import CommitDisposition
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType, ImpactStatus
from thoth.domain.relation import DependencyRelation
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 7, 0, tzinfo=UTC)


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}:{self._next}"


ACTOR = ActorRef(actor_id="human:owner", kind=ActorKind.HUMAN, role="project-owner")


def _staged(identifier: str, value: str, parents: tuple[str, ...] = ()) -> StagedRevision:
    content: dict[str, object] = {"value": value}
    content_digest = domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content))
    snapshot = EntitySnapshot(
        snapshot_id=f"snapshot:{identifier}",
        project_id="project:restore",
        entity_type=EntityType.DECISION_OBJECT,
        entity_id="object:1",
        schema_version="1.0.0",
        content=content,
        content_digest=content_digest,
    )
    revision_payload: dict[str, object] = {
        "identifier": identifier,
        "content_digest": content_digest,
        "parents": list(parents),
    }
    revision = SemanticRevision(
        revision_id=f"revision:{identifier}",
        project_id="project:restore",
        entity_type=EntityType.DECISION_OBJECT,
        entity_id="object:1",
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=parents,
        actor=ACTOR,
        reason=f"set {value}",
        evidence_refs=("span:1",),
        affected_refs=(),
        revision_digest=domain_digest("REVISION", "1.0.0", canonical_payload(revision_payload)),
        created_at=datetime(2026, 8, 30, 7, 0, tzinfo=UTC),
    )
    return StagedRevision(snapshot=snapshot, revision=revision)


def _commit(
    service: RevisionCommitService,
    staged: StagedRevision,
    expected: dict[str, str],
) -> None:
    service.commit(
        RevisionChangeSet(
            changeset_id=f"changeset:{staged.revision.revision_id}",
            project_id="project:restore",
            expected_heads=expected,
            staged_revisions=(staged,),
            impact_plan=ImpactPropagationPlan(),
            actor=ACTOR,
            reason=staged.revision.reason,
        )
    )


def test_restore_creates_child_preserves_history_and_recalculates_downstream(
    tmp_path: Path,
) -> None:
    migrate_sqlite_database(tmp_path / "restore.sqlite3")
    ledger = SqliteLedger(tmp_path / "restore.sqlite3")
    ledger.initialize()
    ids = SequenceIds()
    commits = RevisionCommitService(ledger, FixedClock(), ids, policy_version="policy:1")
    first = _staged("1", "old")
    _commit(commits, first, {})
    second = _staged("2", "current", (first.revision.revision_digest,))
    _commit(
        commits,
        second,
        {"DECISION_OBJECT:object:1": first.revision.revision_digest},
    )
    dependencies = SqliteDependencyGraph(ledger.engine)
    dependencies.add(
        DependencyRelation(
            relation_id="relation:1",
            project_id="project:restore",
            source_ref="DECISION_OBJECT:object:1",
            relation_type="DERIVES",
            target_ref="ACTION:plan:1",
            revision_digest=second.revision.revision_digest,
        )
    )
    dependencies.add(
        DependencyRelation(
            relation_id="relation:2",
            project_id="project:restore",
            source_ref="ACTION:plan:1",
            relation_type="DERIVES",
            target_ref="OUTCOME:1",
            revision_digest=second.revision.revision_digest,
        )
    )
    restores = RestoreService(
        ledger=ledger,
        dependencies=dependencies,
        commits=commits,
        clock=FixedClock(),
        ids=ids,
    )

    result = restores.restore_as_new_revision(
        project_id="project:restore",
        entity_type=EntityType.DECISION_OBJECT,
        entity_id="object:1",
        selected_historical_revision_id="revision:1",
        expected_current_head=second.revision.revision_digest,
        actor=ACTOR,
        reason="restore known-good state",
    )

    assert result.commit.disposition == CommitDisposition.FAST_FORWARD
    assert result.stale_refs == ("ACTION:plan:1", "OUTCOME:1")
    revisions = ledger.read_revisions("project:restore", "DECISION_OBJECT", "object:1")
    assert len(revisions) == 3
    restored = revisions[-1]
    assert restored.parent_revision_digests == (second.revision.revision_digest,)
    restored_snapshot = ledger.read_snapshot(restored.snapshot_id)
    assert restored_snapshot is not None
    assert restored_snapshot.content == first.snapshot.content
    assert restored.snapshot_id != first.snapshot.snapshot_id
    assert ledger.read_dependency_states("project:restore") == {
        "ACTION:plan:1": ImpactStatus.RECALCULATION_REQUIRED,
        "OUTCOME:1": ImpactStatus.RECALCULATION_REQUIRED,
    }
    assert ledger.read_heads("project:restore")["DECISION_OBJECT:object:1"] == (
        restored.revision_digest
    )
    ledger.close()
