"""Compose explicit source policy, scope storage and user-visible resource access."""

from collections.abc import Awaitable, Callable

from pydantic import JsonValue

from thoth.application.commands.operations import OperationCommandHandlers
from thoth.application.commands.resource_scopes import ResourceScopeHandlers
from thoth.application.services import OperationJournal
from thoth.application.services.resource_scope_lineage import (
    ReceiptResourceLineage,
    ResearchResourceLineage,
)
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.application.services.scoped_evidence import ScopedEvidenceGraphStore
from thoth.application.services.scoped_execution import ScopedExecutionStore
from thoth.application.services.scoped_memory import ScopedMemoryCandidates
from thoth.domain.environment import EgressPolicy, EnvironmentProfile
from thoth.domain.operation import OperationRecord
from thoth.domain.resource_scope import ResourceScopePolicy
from thoth.ports.connectors import ConnectorRegistryPort
from thoth.ports.event_store import EventStorePort
from thoth.ports.field_measurement import FieldMeasurementObserverPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.sandbox import SandboxPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry

_LOCAL_SOURCE_KINDS = frozenset({"LOCAL", "GIT"})


def _default_connector_allowlist(
    connectors: ConnectorRegistryPort,
    environment: EnvironmentProfile | None,
) -> tuple[str, ...]:
    if environment is not None:
        return environment.connector_allowlist
    return tuple(
        item.connector_id
        for item in connectors.capabilities()
        if item.source_kind in _LOCAL_SOURCE_KINDS and item.egress_class == "NONE"
    )


def create_resources(
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    projects: ProjectStorePort,
    governance: GovernanceStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> ScopedArtifactLedger:
    raw = stores.artifacts
    scopes = ResourceScopeService(
        store=stores.resource_scopes,
        artifacts=raw,
        projects=projects,
        governance=governance,
        sessions=stores.auth,
        ledger=ledger,
        clock=clock,
        ids=ids,
        evidence=stores.evidence,
    )
    ledger.register_projection("resource-lineage", ResearchResourceLineage(scopes))
    ledger.register_receipt_projection("resource-lineage", ReceiptResourceLineage(ledger, scopes))
    return ScopedArtifactLedger(raw, scopes, ledger)


def create_evidence_access(
    ledger: ManagedLedgerPort, stores: StoreBundlePort, scopes: ResourceScopeService
) -> ScopedEvidenceGraphStore:
    return ScopedEvidenceGraphStore(stores.evidence, scopes, ledger)


def create_memory_access(
    ledger: ManagedLedgerPort, stores: StoreBundlePort, scopes: ResourceScopeService
) -> ScopedMemoryCandidates:
    return ScopedMemoryCandidates(stores.memory_candidates, scopes)


def create_execution_access(
    ledger: ManagedLedgerPort, stores: StoreBundlePort, scopes: ResourceScopeService
) -> ScopedExecutionStore:
    return ScopedExecutionStore(stores.executions, scopes)


def register_resource_handlers(registry: MethodRegistry, scopes: ResourceScopeService) -> None:
    handlers = ResourceScopeHandlers(scopes)
    registry.register("project/source/scope/read", handlers.read)
    registry.register("project/source/scope/grant", handlers.grant)
    registry.register("project/source/scope/revoke", handlers.revoke)
    registry.register("project/source/scope/assign", handlers.assign)
    registry.register("project/source/scope/update", handlers.update)


def scope_bus(
    registry: MethodRegistry,
    ledger: ManagedLedgerPort,
    operations: OperationStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    events: EventStorePort,
    measurement: FieldMeasurementObserverPort | None,
    scopes: ResourceScopeService,
    *,
    seal_abandoned_running: bool = False,
    before_continue: Callable[[str], Awaitable[None]] | None = None,
    queued_admission: Callable[[OperationRecord], dict[str, JsonValue] | None] | None = None,
) -> CommandBus:
    handlers = OperationCommandHandlers(
        operations,
        events,
        clock,
        resource_access=scopes,
        read_ledger=ledger,
    )
    registry.register("operation/read", handlers.read)
    registry.register("operation/checkpoint/read", handlers.checkpoint)
    registry.register("operation/result/read", handlers.result)
    registry.register("operation/pause", handlers.pause)
    registry.register("operation/cancel", handlers.cancel)
    registry.register("operation/resume", handlers.resume)
    return CommandBus(
        registry,
        operations,
        clock,
        ids,
        journal=OperationJournal(events, clock, ids),
        measurement=measurement,
        resource_access=scopes,
        seal_abandoned_running=seal_abandoned_running,
        before_continue=before_continue,
        queued_admission=queued_admission,
    )


def source_policy_defaults(
    connectors: ConnectorRegistryPort,
    environment: EnvironmentProfile | None,
    sandbox: SandboxPort | None,
    resource_scope: ResourceScopePolicy | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": list(_default_connector_allowlist(connectors, environment)),
        "connector_allowed_egress_classes": list(
            ("NONE",)
            if environment is None
            or environment.egress_policy in {EgressPolicy.DENY_ALL, EgressPolicy.MODEL_ONLY}
            else ("NONE", "INTRANET", "ALLOWLISTED_EXTERNAL")
        ),
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": []
        if sandbox is None
        else ["SCRIPTED", "DOCKER_POC", "GVISOR", "FIRECRACKER", "MANAGED"],
        "sandbox_network_policy": "DENY_ALL"
        if environment is None
        else environment.sandbox_network_policy,
        "sandbox_allowed_hosts": []
        if environment is None or environment.sandbox_network_policy == "DENY_ALL"
        else list(environment.allowed_egress_hosts),
        "public_web": {
            "enabled": False,
            "preferred_hosts": [],
            "workspace_grant_id": None,
        },
    }
    if resource_scope is not None:
        payload["resource_scope_policy"] = resource_scope.model_dump(mode="json")
    return payload
