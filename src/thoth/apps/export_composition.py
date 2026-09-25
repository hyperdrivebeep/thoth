"""Composition for receipt, local export and closure persistence boundaries."""

from pathlib import Path

from thoth.adapters.export_bundle import FilesystemExportBundle
from thoth.application.commands.closure_full import ClosureHandlers
from thoth.application.commands.export_full import ExportHandlers
from thoth.application.commands.lifecycle import LifecycleCommandHandlers
from thoth.application.commands.receipts_full import ReceiptHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.export_snapshot import ExportSnapshotResolver
from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.application.services.scoped_receipt_dag import (
    ScopedReceiptControls,
    ScopedReceiptDagService,
    ScopedReceiptDagStore,
)
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.receipt_dag import ReceiptDagStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


def create_export_handlers(
    workspace: Path,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    records: ControlRecordStorePort,
    controls: ControlRecordService,
    artifacts: ArtifactLedgerPort,
    evidence: EvidenceGraphStorePort,
    baselines: BaselineService,
) -> ExportHandlers:
    visible = (
        stores.scoped_ledger(artifacts.scopes)
        if isinstance(artifacts, ScopedArtifactLedger)
        else ledger
    )
    return ExportHandlers(
        bundles=FilesystemExportBundle(workspace),
        resolver=ExportSnapshotResolver(ledger=visible, artifacts=artifacts, evidence=evidence),
        unit_of_work=stores.immediate_uow,
        records=records,
        controls=controls,
        ledger=visible,
        baselines=baselines,
    )


def create_lifecycle_handlers(
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    records: ControlRecordStorePort,
    controls: ControlRecordService,
    artifacts: ArtifactLedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    dag: ReceiptDagService,
    dag_store: ReceiptDagStorePort,
    actions: ActionStorePort,
    executions: ExecutionStorePort,
    outcomes: OutcomeStorePort,
    baselines: BaselineService,
) -> tuple[ReceiptHandlers, LifecycleCommandHandlers, ClosureHandlers]:
    unit_of_work = stores.immediate_uow
    visible = (
        stores.scoped_ledger(artifacts.scopes)
        if isinstance(artifacts, ScopedArtifactLedger)
        else ledger
    )
    public_dag: ReceiptDagService = dag
    public_store: ReceiptDagStorePort = dag_store
    public_records: ControlRecordStorePort = records
    public_controls = controls
    if isinstance(artifacts, ScopedArtifactLedger):
        scoped_dag = ScopedReceiptDagStore(dag_store, ledger, records, artifacts.scopes)
        public_store = scoped_dag
        public_dag = ScopedReceiptDagService(dag, scoped_dag)
        public_records = ScopedReceiptControls(records, scoped_dag)
        public_controls = ControlRecordService(store=public_records, clock=clock, ids=ids)
    return (
        ReceiptHandlers(
            ledger=visible,
            records=public_records,
            controls=public_controls,
            clock=clock,
            ids=ids,
            dag=public_dag,
            dag_store=public_store,
            unit_of_work=unit_of_work,
        ),
        LifecycleCommandHandlers(
            ledger=visible,
            artifacts=artifacts,
            clock=clock,
            ids=ids,
            unit_of_work=unit_of_work,
        ),
        ClosureHandlers(
            records=records,
            controls=controls,
            ledger=visible,
            actions=actions,
            executions=executions,
            outcomes=outcomes,
            baselines=baselines,
            unit_of_work=unit_of_work,
        ),
    )
