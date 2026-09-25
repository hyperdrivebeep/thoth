"""Typed runtime handle and preserved default construction options."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.environment import EnvironmentProfile
from thoth.domain.resource_scope import ResourceScopePolicy
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.improvement import ImprovementEvaluatorPort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.memory import MemoryReviewerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.model_catalog import ModelCatalogPort
from thoth.ports.parser import ParserRegistryPort
from thoth.ports.sandbox import SandboxPort
from thoth.ports.task_profile import TaskProfileCatalogPort
from thoth.protocol.bus import CommandBus


@dataclass(frozen=True)
class RuntimeWithLedger[LedgerT: ManagedLedgerPort]:
    bus: CommandBus
    ledger: LedgerT
    close_storage: Callable[[], None] | None = None

    def close(self) -> None:
        self.bus.close_tasks()
        if self.close_storage is None:
            self.ledger.close()
        else:
            self.close_storage()


AppRuntime = RuntimeWithLedger[SqliteLedger]


class RuntimeOptions(TypedDict, total=False):
    deployment_mode: DeploymentMode | None
    model_catalog: ModelCatalogPort | None
    task_profiles: TaskProfileCatalogPort
    projectpack_root: Path | None
    connector_registry: ConnectorRegistry | None
    sandbox_adapter: SandboxPort | None
    environment_profile: EnvironmentProfile | None
    acquisition_fault_injector: Callable[[str], None] | None
    evidence_fault_injector: Callable[[str], None] | None
    memory_fault_injector: Callable[[str], None] | None
    improvement_evaluator: ImprovementEvaluatorPort | None
    evaluation_catalog: EvaluationCatalogPort | None
    measurement_secret: bytes | None
    model_resolver: ModelResolverPort | None
    parser_registry: ParserRegistryPort | None
    memory_reviewer: MemoryReviewerPort | None
    resource_scope_policy: ResourceScopePolicy | None
