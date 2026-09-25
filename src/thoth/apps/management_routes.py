"""Declarative public routes for project and thread management."""

from thoth.application.commands import (
    InvestigationQueryHandlers,
    ProjectCommandHandlers,
    ThreadCommandHandlers,
)
from thoth.protocol.registry import MethodRegistry


def register_project_methods(registry: MethodRegistry, handlers: ProjectCommandHandlers) -> None:
    registry.register("project/create", handlers.create)
    registry.register("project/list", handlers.list)
    registry.register("project/read", handlers.read)
    registry.register("project/activate", handlers.activate)
    registry.register("project/archive", handlers.archive)
    registry.register("project/delete", handlers.archive)
    registry.register("project/metadata/update", handlers.metadata_update)
    registry.register("project/overlay/update", handlers.overlay_update)
    registry.register("project/policy/read", handlers.policy_read)
    registry.register("project/policy/update", handlers.policy_update)
    registry.register("project/role/list", handlers.role_list)
    registry.register("project/role/assign", handlers.role_assign)
    registry.register("project/role/revoke", handlers.role_revoke)
    registry.register("project/reference/list", handlers.reference_list)
    registry.register("project/reference/import", handlers.reference_import)
    registry.register("project/cutoff/impact", handlers.cutoff_impact)
    registry.register("project/cutoff/update", handlers.cutoff_update)


def register_thread_methods(registry: MethodRegistry, handlers: ThreadCommandHandlers) -> None:
    registry.register("thread/start", handlers.start)
    registry.register("thread/list", handlers.list)
    registry.register("thread/read", handlers.read)
    registry.register("thread/activity/list", handlers.activity_list)
    registry.register("thread/checkpoint/list", handlers.checkpoint_list)
    registry.register("thread/checkpoint/read", handlers.checkpoint_read)
    registry.register("thread/steer", handlers.steer)
    registry.register("thread/pause", handlers.pause)
    registry.register("thread/resume", handlers.resume)
    registry.register("thread/stop", handlers.stop)
    registry.register("thread/fork", handlers.fork)
    registry.register("thread/metadata/update", handlers.metadata_update)


def register_investigation_methods(
    registry: MethodRegistry, handlers: InvestigationQueryHandlers
) -> None:
    registry.register("investigation/read", handlers.read)
    registry.register("investigation/list", handlers.list)
    registry.register("investigation/audit/read", handlers.audit_read)
    registry.register("investigation/start", handlers.start)
    registry.register("investigation/update", handlers.update)
    registry.register("investigation/pause", handlers.pause)
    registry.register("investigation/resume", handlers.resume)
    registry.register("investigation/stop", handlers.stop)
