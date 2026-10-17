"""Observe one cycle publication transaction through stable collaborators and normal RPC."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.atomicity.harness import Snapshot, assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.schema import (
    entity_snapshots,
    hypothesis_records,
    memory_revision_ledger,
    receipts,
    relations,
    semantic_revisions,
)
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork, read_connection
from thoth.application.services.full_project_memory import (
    FullMemoryPromotionResult,
    FullProjectMemoryService,
)
from thoth.application.services.research_identity_service import (
    ResearchIdentityService,
    ResearchOwnershipBatch,
)
from thoth.application.services.revision_service import (
    CommitDisposition,
    CommitResult,
    RevisionCommitService,
)
from thoth.apps.runtime import create_runtime
from thoth.domain.enums import OperationState, ThreadExecutionState
from thoth.domain.memory_preparation import PreparedMemoryPromotion
from thoth.domain.research_execution import research_work
from thoth.domain.revision import RevisionChangeSet


class CycleFault(RuntimeError):
    pass


@dataclass
class TransactionObservation:
    before: Snapshot
    changeset: RevisionChangeSet | None = None


@pytest.mark.parametrize(
    "point", ("after-ledger", "after-research", "after-dependencies", "after-memory")
)
async def test_normal_cycle_failure_rolls_back_all_publication_participants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(one=True, tested=True))
    thread_id = "thread:cycle-fault"
    frames: list[TransactionObservation] = []
    rollbacks: list[TransactionObservation] = []
    fired: list[str] = []
    original_transaction = SqliteAtomicUnitOfWork.transaction
    original_commit = RevisionCommitService.commit
    original_research = ResearchIdentityService.persist_cycle
    original_memory = FullProjectMemoryService.commit_prepared

    @contextmanager
    def transaction(unit: SqliteAtomicUnitOfWork) -> Generator[None]:
        ambient = any(
            value is not None
            for key, value in copy_context().items()
            if key.name == "thoth_sqlite_ambient_transaction"
        )
        if ambient or research_work.get() is None:
            with original_transaction(unit):
                yield
            return
        observed = TransactionObservation(snapshot(runtime.ledger.engine))
        frames.append(observed)
        try:
            with original_transaction(unit):
                yield
        except CycleFault:
            assert observed.changeset is not None
            assert_phase_delta(observed.before, snapshot(runtime.ledger.engine))
            rollbacks.append(observed)
            raise
        finally:
            assert frames.pop() is observed

    def current() -> TransactionObservation:
        assert frames and frames[-1].changeset is not None
        return frames[-1]

    def verify_ledger() -> None:
        changeset = current().changeset
        assert changeset is not None
        expected_revisions = {item.revision.revision_id for item in changeset.staged_revisions}
        expected_snapshots = {item.snapshot.snapshot_id for item in changeset.staged_revisions}
        assert expected_revisions and expected_snapshots
        with read_connection(runtime.ledger.engine) as connection:
            assert (
                set(
                    connection.execute(
                        select(semantic_revisions.c.revision_id).where(
                            semantic_revisions.c.revision_id.in_(expected_revisions)
                        )
                    ).scalars()
                )
                == expected_revisions
            )
            assert (
                set(
                    connection.execute(
                        select(entity_snapshots.c.snapshot_id).where(
                            entity_snapshots.c.snapshot_id.in_(expected_snapshots)
                        )
                    ).scalars()
                )
                == expected_snapshots
            )

    def fail() -> None:
        verify_ledger()
        fired.append(point)
        raise CycleFault("synthetic cycle publication fault")

    def commit(service: RevisionCommitService, changeset: RevisionChangeSet) -> CommitResult:
        matched = changeset.project_id == "p" and changeset.reason == f"thread cycle {thread_id}"
        if matched:
            assert frames
            frames[-1].changeset = changeset
        result = original_commit(service, changeset)
        if matched:
            assert result.disposition is CommitDisposition.FAST_FORWARD
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    connection.execute(
                        select(receipts.c.receipt_id).where(
                            receipts.c.receipt_id == result.receipt.receipt_id
                        )
                    ).scalar_one()
                    == result.receipt.receipt_id
                )
            if point == "after-ledger":
                fail()
        return result

    def research(service: ResearchIdentityService, batch: ResearchOwnershipBatch) -> None:
        original_research(service, batch)
        if frames and frames[-1].changeset is not None and point == "after-research":
            expected = {item.revision_digest for item in batch.hypotheses.hypotheses}
            assert expected
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    set(
                        connection.execute(
                            select(hypothesis_records.c.revision_digest).where(
                                hypothesis_records.c.revision_digest.in_(expected)
                            )
                        ).scalars()
                    )
                    == expected
                )
            fail()

    def memory(
        service: FullProjectMemoryService,
        prepared: PreparedMemoryPromotion,
        *,
        expected_head: str | None = None,
    ) -> FullMemoryPromotionResult:
        matched = bool(frames and frames[-1].changeset is not None)
        if matched and point == "after-dependencies":
            changeset = current().changeset
            assert changeset is not None
            digests = {item.revision.revision_digest for item in changeset.staged_revisions}
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    len(
                        connection.execute(
                            select(relations.c.relation_id).where(
                                relations.c.revision_digest.in_(digests)
                            )
                        ).all()
                    )
                    >= 2
                )
            fail()
        result = original_memory(service, prepared, expected_head=expected_head)
        if matched and point == "after-memory":
            expected = {item.memory_revision_id for item in prepared.revisions}
            assert expected
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    set(
                        connection.execute(
                            select(memory_revision_ledger.c.memory_revision_id).where(
                                memory_revision_ledger.c.memory_revision_id.in_(expected)
                            )
                        ).scalars()
                    )
                    == expected
                )
            fail()
        return result

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteAtomicUnitOfWork, "transaction", transaction)
            patch.setattr(RevisionCommitService, "commit", commit)
            patch.setattr(ResearchIdentityService, "persist_cycle", research)
            patch.setattr(FullProjectMemoryService, "commit_prepared", memory)
            accepted = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/start",
                        "cycle-fault",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "problem": "Compare the connected condition",
                            "contract_version": 2,
                        },
                    )
                )
            )
            await runtime.bus.drain()
        assert fired == [point] and len(rollbacks) == 1 and frames == []
        operation_id = accepted["operation_id"]
        assert isinstance(operation_id, str)
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None and operation.state is OperationState.FAILED
        assert (
            operation.error is not None
            and operation.error["message"] == "research execution failed"
        )
        from tests.integration.test_research_focus_basis import research_host

        thread = research_host(runtime).threads.read(thread_id)
        assert thread is not None and thread.execution_state is ThreadExecutionState.IDLE
        saved = snapshot(runtime.ledger.engine)
        if point == "after-memory":
            accepted_again = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "cycle-recovery",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "instruction": "Review the connected condition again",
                            "contract_version": 2,
                        },
                    )
                )
            )
            await runtime.bus.drain()
            next_id = accepted_again["operation_id"]
            assert isinstance(next_id, str)
            recovered = runtime.bus.read_operation(next_id)
            assert recovered is not None and recovered.state is OperationState.SUCCEEDED
            current_db = snapshot(runtime.ledger.engine)
            assert len(current_db["semantic_revisions"]) > len(saved["semantic_revisions"])
            saved = current_db
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(saved, snapshot(reopened.ledger.engine))
        failed = rollbacks[0].changeset
        assert failed is not None
        with reopened.ledger.engine.connect() as connection:
            assert not connection.execute(
                select(semantic_revisions.c.revision_id).where(
                    semantic_revisions.c.revision_id.in_(
                        [item.revision.revision_id for item in failed.staged_revisions]
                    )
                )
            ).all()
            assert not connection.execute(
                select(entity_snapshots.c.snapshot_id).where(
                    entity_snapshots.c.snapshot_id.in_(
                        [item.snapshot.snapshot_id for item in failed.staged_revisions]
                    )
                )
            ).all()
    finally:
        reopened.close()
