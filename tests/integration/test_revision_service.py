from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from thoth.adapters.storage import SqliteLedger, migrate_sqlite_database
from thoth.application.services import RevisionCommitService
from thoth.application.services.revision_service import CommitDisposition
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
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
        return datetime(2026, 8, 30, 0, 0, tzinfo=UTC)


class SequentialIds:
    def __init__(self) -> None:
        self._value = 0

    def new(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}:{self._value}"


def _staged(identifier: str, value: str, *, parents: tuple[str, ...] = ()) -> StagedRevision:
    content: dict[str, object] = {"value": value}
    content_digest = domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content))
    snapshot = EntitySnapshot(
        snapshot_id=f"snapshot:{identifier}",
        project_id="project:1",
        entity_type=EntityType.DECISION_OBJECT,
        entity_id="object:1",
        schema_version="1.0.0",
        content=content,
        content_digest=content_digest,
    )
    revision_payload = {
        "snapshot": content_digest,
        "parents": list(parents),
        "actor": "agent:1",
        "reason": value,
    }
    revision_digest = domain_digest(
        "REVISION",
        "1.0.0",
        canonical_payload(revision_payload, set_paths=frozenset({("parents",)})),
    )
    revision = SemanticRevision(
        revision_id=f"revision:{identifier}",
        project_id="project:1",
        entity_type=EntityType.DECISION_OBJECT,
        entity_id="object:1",
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=parents,
        actor=ActorRef(actor_id="agent:1", kind=ActorKind.AGENT, role="state-reconstructor"),
        reason=value,
        evidence_refs=("span:1",),
        affected_refs=(),
        revision_digest=revision_digest,
        created_at=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
    )
    return StagedRevision(snapshot=snapshot, revision=revision)


def _service(path: Path) -> tuple[SqliteLedger, RevisionCommitService]:
    migrate_sqlite_database(path)
    ledger = SqliteLedger(path)
    ledger.initialize()
    service = RevisionCommitService(
        ledger,
        FixedClock(),
        SequentialIds(),
        policy_version="policy:1",
    )
    return ledger, service


def test_initial_commit_fast_forwards_and_writes_receipt(tmp_path: Path) -> None:
    ledger, service = _service(tmp_path / "ledger.sqlite3")
    staged = _staged("1", "initial")
    result = service.commit(
        RevisionChangeSet(
            changeset_id="changeset:1",
            project_id="project:1",
            expected_heads={},
            staged_revisions=(staged,),
            impact_plan=ImpactPropagationPlan(),
            actor=staged.revision.actor,
            reason="initial",
        )
    )

    assert result.disposition == CommitDisposition.FAST_FORWARD
    assert ledger.read_heads("project:1") == {
        "DECISION_OBJECT:object:1": staged.revision.revision_digest
    }
    assert ledger.read_revisions("project:1", "DECISION_OBJECT", "object:1") == (staged.revision,)
    receipts = ledger.read_receipts("project:1")
    assert receipts == (result.receipt,)
    assert receipts[0].semantic_truth_certified is False
    ledger.close()


def test_stale_expected_head_creates_branch_without_moving_head(tmp_path: Path) -> None:
    ledger, service = _service(tmp_path / "ledger.sqlite3")
    first = _staged("1", "initial")
    service.commit(
        RevisionChangeSet(
            changeset_id="changeset:1",
            project_id="project:1",
            expected_heads={},
            staged_revisions=(first,),
            impact_plan=ImpactPropagationPlan(),
            actor=first.revision.actor,
            reason="initial",
        )
    )
    branch = _staged("2", "parallel", parents=("f" * 64,))
    result = service.commit(
        RevisionChangeSet(
            changeset_id="changeset:2",
            project_id="project:1",
            expected_heads={"DECISION_OBJECT:object:1": "f" * 64},
            staged_revisions=(branch,),
            impact_plan=ImpactPropagationPlan(),
            actor=branch.revision.actor,
            reason="parallel",
        )
    )

    assert result.disposition == CommitDisposition.BRANCH
    assert result.before_head_set_digest == result.after_head_set_digest
    assert ledger.read_heads("project:1")["DECISION_OBJECT:object:1"] == (
        first.revision.revision_digest
    )
    assert len(ledger.read_revisions("project:1", "DECISION_OBJECT", "object:1")) == 2
    ledger.close()


def test_exact_head_set_expectation_detects_concurrent_unrelated_head_inside_commit(
    tmp_path: Path,
) -> None:
    ledger, service = _service(tmp_path / "ledger.sqlite3")
    first = _staged("1", "initial")
    service.commit(
        RevisionChangeSet(
            changeset_id="changeset:1",
            project_id="project:1",
            expected_heads={},
            staged_revisions=(first,),
            impact_plan=ImpactPropagationPlan(),
            actor=first.revision.actor,
            reason="initial",
        )
    )
    sealed_heads = dict(ledger.read_heads("project:1"))
    with ledger.transaction() as transaction:
        transaction.set_head("project:1", "HYPOTHESIS:concurrent", "e" * 64)
    candidate = _staged("2", "candidate", parents=(first.revision.revision_digest,))
    result = service.commit(
        RevisionChangeSet(
            changeset_id="changeset:2",
            project_id="project:1",
            expected_heads={"DECISION_OBJECT:object:1": first.revision.revision_digest},
            expected_head_set_digest=head_set_digest(sealed_heads),
            staged_revisions=(candidate,),
            impact_plan=ImpactPropagationPlan(),
            actor=candidate.revision.actor,
            reason="must branch on full HeadSet drift",
        )
    )

    assert result.disposition == CommitDisposition.BRANCH
    assert ledger.read_heads("project:1")["DECISION_OBJECT:object:1"] == (
        first.revision.revision_digest
    )
    assert ledger.read_heads("project:1")["HYPOTHESIS:concurrent"] == "e" * 64
    ledger.close()


def test_transaction_rolls_back_all_rows_on_integrity_failure(tmp_path: Path) -> None:
    ledger, service = _service(tmp_path / "ledger.sqlite3")
    first = _staged("same", "first")
    second = _staged("same", "second")
    with pytest.raises(IntegrityError):
        service.commit(
            RevisionChangeSet(
                changeset_id="changeset:failure",
                project_id="project:1",
                expected_heads={},
                staged_revisions=(first, second),
                impact_plan=ImpactPropagationPlan(),
                actor=first.revision.actor,
                reason="must roll back",
            )
        )

    assert ledger.read_heads("project:1") == {}
    assert ledger.read_revisions("project:1", "DECISION_OBJECT", "object:1") == ()
    assert ledger.read_receipts("project:1") == ()
    ledger.close()
