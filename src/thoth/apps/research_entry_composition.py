"""Compose research admission, its worker lifetime, and user model preferences."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from thoth.adapters.models.catalog import (
    CodexModelCatalog,
    CompositeModelCatalog,
    StaticModelCatalog,
)
from thoth.adapters.models.codex_account_usage import CodexAccountUsageAdapter
from thoth.adapters.models.local_credentials import LocalModelCredentials, ThothLocalCatalog
from thoth.adapters.models.xai_account_usage import XaiAccountUsageAdapter
from thoth.adapters.storage.research_queue import ControlResearchQueueStore
from thoth.adapters.storage.workspace_setup import FilesystemWorkspaceSetup
from thoth.adapters.task_profiles import default_task_profiles
from thoth.adapters.worker_identity import LocalWorkerIdentity
from thoth.application.commands.model_settings import ModelSettingsHandlers
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.commands.threads import ThreadCommandHandlers
from thoth.application.commands.workspace_setup import WorkspaceSetupHandlers
from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.model_settings import ModelSettingsService
from thoth.application.services.operation_journal import OperationJournal
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_analysis import ResearchAnalysis
from thoth.application.services.research_leases import ResearchLeases
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.test_runtime import TestComponents
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.model import ModelResolverPort
from thoth.ports.model_catalog import ModelCatalogPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.ports.task_profile import TaskProfileCatalogPort
from thoth.protocol.registry import MethodRegistry


@dataclass
class ResearchEntryComposition:
    entry: ResearchThreadHandlers
    worker: LocalWorkerIdentity
    stores: StoreBundlePort

    def close_storage(self) -> None:
        self.worker.close()
        self.stores.close()


def _configured_model_available(
    catalog: ModelCatalogPort, credentials: LocalModelCredentials
) -> bool:
    key_models = {
        (item["provider"], item["model"]) for item in credentials.list_credentials()
    }
    oauth_providers: set[str] = set()
    for account in credentials.account_connections():
        if account.get("oauth") is not True:
            continue
        providers = account.get("available_model_providers")
        if not isinstance(providers, list):
            continue
        oauth_providers.update(
            item for item in cast(list[object], providers) if isinstance(item, str)
        )
    return any(
        (option.provider, option.model) in key_models
        or option.provider in oauth_providers
        for option in catalog.options()
    )


def create_research_entry(
    registry: MethodRegistry,
    stores: StoreBundlePort,
    legacy: ThreadCommandHandlers,
    artifacts: ScopedArtifactLedger,
    criteria: CriterionContractStorePort,
    connectors: ConnectorService,
    memory: FullProjectMemoryService,
    evidence: EvidenceGraphStorePort,
    models: ModelResolverPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    task_profiles: TaskProfileCatalogPort | None,
    catalog: ModelCatalogPort | None,
    use_codex_defaults: bool,
    tests: TestComponents | None = None,
    workspace: Path | None = None,
    deployment_mode: DeploymentMode | None = None,
) -> ResearchEntryComposition:
    records = RequestRecords(
        stores.ledger,
        stores.controls,
        clock,
        ids,
        events=OperationJournal(stores.events, clock, ids),
    )
    worker = LocalWorkerIdentity()
    settings = ModelSettingsService(
        records,
        catalog
        or (
            CompositeModelCatalog(
                ThothLocalCatalog(workspace), CodexModelCatalog()
            )
            if use_codex_defaults
            else StaticModelCatalog()
        ),
    )
    local_credentials = LocalModelCredentials(workspace)
    settings_handlers = ModelSettingsHandlers(
        settings,
        stores.projects,
        legacy.authorize_before_claim,
        stores.threads,
        local_credentials,
        unregistered_default_is_available=not use_codex_defaults,
    )
    registry.register("model/settings/read", settings_handlers.read)
    registry.register("model/settings/update", settings_handlers.update)
    registry.register("model/credential/list", settings_handlers.list_credentials)
    registry.register("model/credential/register", settings_handlers.register_credential)
    mode = deployment_mode or parse_deployment_mode()
    ready_projection = None
    if mode is DeploymentMode.HOSTED_REVIEW:
        from thoth.apps.hosted_review_composition import hosted_ready_projection

        ready_projection = hosted_ready_projection
    workspace_setup = WorkspaceSetupHandlers(
        FilesystemWorkspaceSetup(workspace),
        deployment_mode=mode,
        model_connected=lambda: _configured_model_available(settings.catalog, local_credentials),
        ready_projection=ready_projection,
    )
    registry.register("workspace/setup/read", workspace_setup.read)
    registry.register("workspace/setup/update", workspace_setup.update)
    registry.register("workspace/ready", workspace_setup.ready)
    entry = ResearchThreadHandlers(
        legacy=legacy,
        analyze=registry.resolve("thread/input"),
        records=records,
        analysis=ResearchAnalysis(
            records,
            artifacts,
            stores.governance,
            criteria,
            connectors,
            memory,
            evidence,
            task_profiles or default_task_profiles(),
        ),
        projects=stores.projects,
        threads=stores.threads,
        governance=stores.governance,
        operations=stores.operations,
        models=models,
        access=artifacts.scopes,
        leases=ResearchLeases(records, worker),
        model_settings=settings,
        queue_store=ControlResearchQueueStore(stores.controls, clock, ids),
        assessment_verifier=None if tests is None else tests.lifecycle.require_assessment,
        provider_usage=CodexAccountUsageAdapter(),
    )
    entry.provider_usage.adapters = {
        "codex-oauth": CodexAccountUsageAdapter(),
        "default": CodexAccountUsageAdapter(),
        "xai": XaiAccountUsageAdapter(),
    }
    entry.queue.reconcile_terminal_operations(entry)
    registry.decorate("thread/start", lambda _: entry.start)
    registry.decorate("thread/input", lambda _: entry.input)
    registry.decorate("thread/read", lambda _: entry.read)
    registry.decorate("thread/steer", lambda _: entry.steer)
    registry.decorate("thread/activity/list", lambda _: entry.activity_list)
    registry.decorate("thread/pause", lambda _: entry.pause)
    registry.decorate("thread/resume", lambda _: entry.resume)
    registry.decorate("thread/stop", lambda _: entry.stop)
    for method in ("thread/start", "thread/input", "thread/steer"):
        registry.register_reentry_authorizer(method, entry.authorize_reentry)
    if mode is DeploymentMode.HOSTED_REVIEW:
        from thoth.apps.hosted_review_composition import install_hosted_review_rpc_guards
        from thoth.apps.hosted_review_quota import HostedReviewLimits

        limits = HostedReviewLimits()
        install_hosted_review_rpc_guards(
            registry,
            projects=stores.projects,
            operations=stores.operations,
            records=records,
            max_projects=limits.max_projects,
            max_concurrent_research=limits.max_concurrent_research,
        )
    return ResearchEntryComposition(entry, worker, stores)
