"""One composition owner for history projections and strict restore services."""

from thoth.adapters.storage.history_cursors import OpaqueHistoryCursors
from thoth.application.commands.research_followup import ResearchFollowupHandlers
from thoth.application.commands.research_history import ResearchHistoryHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.historical_result import HistoricalResultReader
from thoth.application.services.history_projection import HistoryProjection
from thoth.application.services.research_followup_projection import ProjectReviewReader
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.research_history import ResearchHistoryService
from thoth.application.services.research_history_scope import HistoryScopeValidator
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.application.services.selected_evidence_reader import SelectedEvidenceReader
from thoth.apps.restore_profiles import restore_profiles
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.registry import MethodRegistry

# Scoped H3/H5 normal-entry, access, CAS, rollback and consumer checks are recorded in
# docs/verification/research-history-restore-20260921.md. This is not global owner closure.
RESTORE_APPLY_READY = True


def register_research_history(
    registry: MethodRegistry, stores: StoreBundlePort, access: ResourceScopeService
) -> None:
    ledger = stores.scoped_ledger(access)
    projection = HistoryProjection(
        ledger,
        stores.research_history,
        access,
        ResearchFreshnessService(
            ledger, stores.projects, stores.governance, stores.artifacts, stores.operations
        ),
        restore_profiles(),
        RESTORE_APPLY_READY,
    )
    scopes = HistoryScopeValidator(ledger, access, stores.projects, stores.threads)
    freshness = ResearchFreshnessService(
        ledger, stores.projects, stores.governance, stores.artifacts, stores.operations
    )
    handlers = ResearchHistoryHandlers(
        ResearchHistoryService(projection, scopes, OpaqueHistoryCursors()),
        HistoricalResultReader(
            projection,
            scopes,
            stores.operations,
            stores.controls,
            SelectedEvidenceReader(ScopedArtifactLedger(stores.artifacts, access, ledger)),
        ),
    )
    registry.register("revision/timeline/read", handlers.timeline_read)
    registry.register("revision/timeline/item/read", handlers.item_read)
    registry.register("thread/result/read", handlers.result_read)
    followup = ResearchFollowupHandlers(
        results=handlers.results,
        reviews=ProjectReviewReader(
            ledger=ledger,
            projects=stores.projects,
            threads=stores.threads,
            governance=stores.governance,
            operations=stores.operations,
            access=access,
            freshness=freshness,
        ),
        scopes=scopes,
    )
    registry.register("thread/result/compare/read", followup.compare_read)
    registry.register("project/review/list", followup.review_list)


def register_restore(
    registry: MethodRegistry,
    stores: StoreBundlePort,
    access: ResourceScopeService,
    baselines: BaselineService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> None:
    from thoth.application.commands.restore import RestoreHandlers
    from thoth.application.services.control_record_service import ControlRecordService
    from thoth.application.services.operation_journal import OperationJournal
    from thoth.application.services.restore_impact import RestoreImpactPlanner
    from thoth.application.services.restore_planner import RestorePlanner
    from thoth.application.services.restore_publication import RestorePublication
    from thoth.application.services.restore_references import (
        HypothesisRestoreReferences,
        PlanRestoreReferences,
        RestoreReferenceRegistry,
    )
    from thoth.application.services.restore_source_basis import (
        ImmutableSpanRestoreSources,
        ManifestRestoreSources,
        RegisteredRestoreSources,
    )
    from thoth.application.services.revision_service import RevisionCommitService
    from thoth.application.services.scoped_revision_controls import ScopedRevisionControls
    from thoth.apps.restore_profiles import restore_profiles
    from thoth.domain.action_full import ActionPlanRecord
    from thoth.domain.enums import EntityType
    from thoth.domain.execution_full import PlanExecutionRecord
    from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord

    profiles = restore_profiles()
    hypothesis_refs = HypothesisRestoreReferences(
        stores.hypotheses(),
        stores.test_validity,
        stores.ledger,
        access,
        stores.projects,
        stores.executions,
    )
    plan_refs = PlanRestoreReferences(stores.actions(), stores.ledger, access)
    planner = RestorePlanner(
        ledger=stores.ledger,
        profiles=profiles,
        impact=RestoreImpactPlanner(
            stores.ledger,
            stores.dependencies,
            profiles,
            access,
            ((EntityType.EXECUTION, PlanExecutionRecord, "plan_execution_id"),),
        ),
        access=access,
        projects=stores.projects,
        governance=stores.governance,
        artifacts=stores.artifacts,
        scopes=stores.resource_scopes,
        threads=stores.threads,
        executions=stores.executions,
        controls=stores.controls,
        baselines=stores.baselines,
        history=stores.research_history,
        sources=RegisteredRestoreSources(
            (
                ManifestRestoreSources(stores.ledger, stores.research_history),
                ImmutableSpanRestoreSources(stores.research_history),
            )
        ),
        references=RestoreReferenceRegistry(
            {
                HypothesisRecord: hypothesis_refs.hypothesis,
                HypothesisPortfolioRecord: hypothesis_refs.portfolio,
                ActionPlanRecord: plan_refs.resolve,
            }
        ),
        apply_ready=RESTORE_APPLY_READY,
    )
    controls = ControlRecordService(
        store=ScopedRevisionControls(stores.controls, stores.ledger, access), clock=clock, ids=ids
    )
    publication = RestorePublication(
        planner,
        RevisionCommitService(stores.ledger, clock, ids, policy_version="restore:2.0.0"),
        baselines,
        controls,
        stores.operations,
        OperationJournal(stores.events, clock, ids),
        clock,
        ids,
    )
    handlers = RestoreHandlers(planner, publication, registry.resolve("revision/diff/read"))
    registry.decorate("revision/restore/preview", lambda _: handlers.preview)
    registry.decorate("revision/diff/read", lambda _: handlers.diff)
    registry.decorate("revision/restore", lambda _: handlers.legacy_apply)
    registry.register("revision/restore/apply", handlers.apply)
    from thoth.application.commands.restore_proposals import RestoreProposalHandlers

    proposals = RestoreProposalHandlers(
        handlers,
        registry.resolve("revision/changeSet/validate"),
        registry.resolve("revision/changeSet/commit"),
    )
    registry.decorate("revision/restore/propose", lambda _: proposals.propose)
    registry.decorate("revision/changeSet/validate", lambda _: proposals.validate)
    registry.decorate("revision/changeSet/commit", lambda _: proposals.commit)
