"""Compose revision and receipt/lifecycle owners without changing their scoped views."""

from __future__ import annotations

from dataclasses import dataclass

from thoth.application.commands.closure_full import ClosureHandlers
from thoth.application.commands.lifecycle import LifecycleCommandHandlers
from thoth.application.commands.receipts_full import ReceiptHandlers
from thoth.application.commands.revisions import RevisionCommandHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.export_composition import create_lifecycle_handlers
from thoth.apps.revision_composition import create_revision_handlers
from thoth.ports.action import ActionStorePort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.receipt_dag import ReceiptDagStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


@dataclass(frozen=True)
class HistoryCommands:
    revision_handlers: RevisionCommandHandlers
    revision_full_handlers: RevisionFullHandlers
    receipt_handlers: ReceiptHandlers
    lifecycle_handlers: LifecycleCommandHandlers
    closure_handlers: ClosureHandlers


def create_history_commands(
    *,
    action_store: ActionStorePort,
    artifact_ledger: ScopedArtifactLedger,
    baseline_service: BaselineService,
    clock: ClockPort,
    control_service: ControlRecordService,
    control_store: ControlRecordStorePort,
    dependency_graph: DependencyGraphPort,
    execution_store: ExecutionStorePort,
    governance: GovernanceStorePort,
    ids: IdGeneratorPort,
    ledger: ManagedLedgerPort,
    outcome_store: OutcomeStorePort,
    receipt_dag_service: ReceiptDagService,
    receipt_dag_store: ReceiptDagStorePort,
    stores: StoreBundlePort,
) -> HistoryCommands:
    revision_handlers, revision_full_handlers = create_revision_handlers(
        ledger,
        stores,
        control_store,
        control_service,
        dependency_graph,
        governance,
        clock,
        ids,
        baseline_service,
        artifact_ledger.scopes,
    )
    receipt_handlers, lifecycle_handlers, closure_handlers = create_lifecycle_handlers(
        stores=stores,
        ledger=ledger,
        records=control_store,
        controls=control_service,
        artifacts=artifact_ledger,
        clock=clock,
        ids=ids,
        dag=receipt_dag_service,
        dag_store=receipt_dag_store,
        actions=action_store,
        executions=execution_store,
        outcomes=outcome_store,
        baselines=baseline_service,
    )
    return HistoryCommands(
        revision_handlers=revision_handlers,
        revision_full_handlers=revision_full_handlers,
        receipt_handlers=receipt_handlers,
        lifecycle_handlers=lifecycle_handlers,
        closure_handlers=closure_handlers,
    )
