"""Compose public revision reads with scoped payloads and the shared canonical writer."""

from thoth.application.commands.revisions import RevisionCommandHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.scoped_revision_controls import ScopedRevisionControls
from thoth.application.services.semantic_merge import SemanticThreeWayMergeService
from thoth.apps.research_history_composition import register_research_history, register_restore
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.registry import MethodRegistry


def create_revision_handlers(
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    records: ControlRecordStorePort,
    controls: ControlRecordService,
    dependencies: DependencyGraphPort,
    governance: GovernanceStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    baselines: BaselineService,
    access: ResourceScopeService,
) -> tuple[RevisionCommandHandlers, RevisionFullHandlers]:
    visible = stores.scoped_ledger(access)
    scoped_records = ScopedRevisionControls(records, ledger, access)
    scoped_controls = ControlRecordService(store=scoped_records, clock=clock, ids=ids)
    return (
        RevisionCommandHandlers(
            ledger=visible, dependencies=dependencies, clock=clock, ids=ids, baselines=baselines
        ),
        RevisionFullHandlers(
            records=scoped_records,
            controls=scoped_controls,
            ledger=visible,
            dependencies=dependencies,
            governance=governance,
            commits=RevisionCommitService(ledger, clock, ids, policy_version="revision:2.0.0"),
            clock=clock,
            ids=ids,
            semantic_merge=SemanticThreeWayMergeService(
                ledger=visible,
                dependencies=dependencies,
                commits=RevisionCommitService(
                    ledger, clock, ids, policy_version="semantic-merge:1.0.0"
                ),
                clock=clock,
                ids=ids,
            ),
            baselines=baselines,
            history_reader=stores.research_history,
        ),
    )


def register_revision_methods(
    registry: MethodRegistry,
    revision_handlers: RevisionCommandHandlers,
    revision_full_handlers: RevisionFullHandlers,
    stores: StoreBundlePort,
    access: ResourceScopeService,
    baseline_service: BaselineService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> None:
    register_research_history(registry, stores, access)
    registry.register("revision/history/read", revision_handlers.history)
    registry.register("revision/compare", revision_handlers.compare)
    registry.register("revision/restore", revision_handlers.restore)
    registry.register("revision/list", revision_full_handlers.list)
    registry.register("revision/read", revision_full_handlers.read)
    registry.register("revision/content/read", revision_full_handlers.content_read)
    registry.register("revision/diff/read", revision_full_handlers.diff_read)
    registry.register("revision/graph/read", revision_full_handlers.graph_read)
    registry.register("revision/head/read", revision_full_handlers.head_read)
    registry.register("revision/changeSet/read", revision_full_handlers.change_set_read)
    registry.register("revision/impact/read", revision_full_handlers.impact_read)
    registry.register("revision/conflict/list", revision_full_handlers.conflict_list)
    registry.register("revision/conflict/read", revision_full_handlers.conflict_read)
    registry.register("revision/restore/preview", revision_full_handlers.restore_preview)
    registry.register("revision/baseline/list", revision_full_handlers.baseline_list)
    registry.register("revision/baseline/read", revision_full_handlers.baseline_read)
    registry.register("revision/audit/read", revision_full_handlers.audit_read)
    registry.register("revision/propose", revision_full_handlers.propose)
    registry.register("revision/changeSet/create", revision_full_handlers.change_set_create)
    registry.register("revision/changeSet/validate", revision_full_handlers.change_set_validate)
    registry.register("revision/changeSet/commit", revision_full_handlers.change_set_commit)
    registry.register("revision/branch/create", revision_full_handlers.branch_create)
    registry.register("revision/merge/propose", revision_full_handlers.merge_propose)
    registry.register("revision/merge/resolve", revision_full_handlers.merge_resolve)
    registry.register("revision/restore/propose", revision_full_handlers.restore_propose)
    registry.register("revision/recompute/request", revision_full_handlers.recompute_request)
    registry.register("revision/baseline/prepare", revision_full_handlers.baseline_prepare)
    registry.register("revision/baseline/decide", revision_full_handlers.baseline_decide)
    register_restore(registry, stores, access, baseline_service, clock, ids)
