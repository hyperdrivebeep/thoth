"""Compose sandbox routing and execution services on the existing store owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thoth.adapters.sandbox import DisabledSandboxAdapter
from thoth.application.services.action_service import ActionService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.ingestion_service import IngestionService
from thoth.application.services.policy_gate import PolicyGate
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.application.services.sandbox_router import RegisteredSandboxRouter
from thoth.application.services.sandbox_service import SandboxService
from thoth.apps.test_runtime import create_execution_components
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.sandbox import SandboxPort
from thoth.ports.store_bundle import StoreBundlePort

if TYPE_CHECKING:
    from thoth.apps.research_runtime import ResearchComponents


@dataclass(frozen=True)
class SandboxComponents:
    sandbox_service: SandboxService
    execution_service: ExecutionService
    r2_closed_loop: R2ClosedLoopCoordinator


def create_sandbox_components(
    *,
    sandbox_adapter: SandboxPort | None,
    artifact_ledger: ArtifactLedgerPort,
    object_store: ObjectStorePort,
    control_service: ControlRecordService,
    ingestion_service: IngestionService,
    projects: ProjectStorePort,
    governance: ProjectPolicyReaderPort,
    ledger: ManagedLedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    stores: StoreBundlePort,
    research_components: ResearchComponents,
    action_store: ActionStorePort,
    action_service: ActionService,
    execution_store: ExecutionStorePort,
) -> SandboxComponents:
    effective_sandbox = sandbox_adapter or DisabledSandboxAdapter()
    sandbox_router = RegisteredSandboxRouter()
    sandbox_capability = effective_sandbox.capability
    sandbox_router.register(
        sandbox_capability.runtime_profile,
        effective_sandbox,
        available=sandbox_capability.available,
        version=sandbox_capability.runtime_version,
        security_tier=sandbox_capability.security_tier,
        network_modes=sandbox_capability.network_modes,
    )
    sandbox_service = SandboxService(
        router=sandbox_router,
        artifacts=artifact_ledger,
        objects=object_store,
        controls=control_service,
        ingestion=ingestion_service,
        projects=projects,
        policies=PolicyGate(policies=governance, clock=clock),
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
    execution_service, r2_closed_loop = create_execution_components(
        stores=stores,
        research=research_components,
        actions=action_store,
        action_service=action_service,
        executions=execution_store,
        artifacts=artifact_ledger,
        sandbox=sandbox_service,
        ledger=ledger,
        policies=governance,
        clock=clock,
        ids=ids,
    )
    return SandboxComponents(
        sandbox_service=sandbox_service,
        execution_service=execution_service,
        r2_closed_loop=r2_closed_loop,
    )
