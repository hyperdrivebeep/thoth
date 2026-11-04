"""Register the permanent project deletion command; the delete itself is local-only."""

from thoth.application.commands.project_deletion import (
    PERMANENT_DELETE_METHOD,
    ProjectDeletionHandlers,
)
from thoth.domain.deployment_mode import DeploymentMode
from thoth.ports.runtime import ClockPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.registry import MethodRegistry


def register_project_deletion(
    registry: MethodRegistry,
    stores: StoreBundlePort,
    clock: ClockPort,
    mode: DeploymentMode,
) -> ProjectDeletionHandlers:
    handlers = ProjectDeletionHandlers(
        store=stores.projects,
        purge=stores.project_purge,
        ledger=stores.ledger,
        sessions=stores.auth,
        governance=stores.governance,
        clock=clock,
        mode=mode,
    )
    registry.register(PERMANENT_DELETE_METHOD, handlers.permanent_delete)
    return handlers
