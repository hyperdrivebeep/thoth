"""Register closure and export RPCs against their existing handler instances."""

from thoth.application.commands.closure_full import ClosureHandlers
from thoth.application.commands.export_full import ExportHandlers
from thoth.application.commands.lifecycle import LifecycleCommandHandlers
from thoth.protocol.registry import MethodRegistry


def register_closure_and_export_methods(
    registry: MethodRegistry,
    lifecycle_handlers: LifecycleCommandHandlers,
    closure_handlers: ClosureHandlers,
    export_handlers: ExportHandlers,
) -> None:
    registry.register("closure/prepare", lifecycle_handlers.prepare_closure)
    registry.register("closure/read", lifecycle_handlers.read_closure)
    registry.register("closure/finalize", lifecycle_handlers.finalize_closure)
    registry.register("closure/list", closure_handlers.list)
    registry.register("closure/readiness/read", closure_handlers.readiness_read)
    registry.register("closure/package/read", closure_handlers.package_read)
    registry.register("closure/openItem/list", closure_handlers.open_item_list)
    registry.register("closure/reopen/read", closure_handlers.reopen_read)
    registry.register("closure/retention/read", closure_handlers.retention_read)
    registry.register("closure/audit/read", closure_handlers.audit_read)
    registry.register("closure/readiness/assess", closure_handlers.readiness_assess)
    registry.register("closure/decide", closure_handlers.decide)
    registry.register("closure/followup/create", closure_handlers.followup_create)
    registry.register("closure/reopen", closure_handlers.reopen)
    registry.register("closure/retention/plan", closure_handlers.retention_plan)
    registry.register("closure/purge/prepare", closure_handlers.purge_prepare)
    registry.register("export/prepare", lifecycle_handlers.prepare_export)
    registry.register("export/list", export_handlers.list)
    registry.register("export/read", export_handlers.read)
    registry.register("export/plan/read", export_handlers.plan_read)
    registry.register("export/snapshot/read", export_handlers.snapshot_read)
    registry.register("export/manifest/read", export_handlers.manifest_read)
    registry.register("export/artifact/list", export_handlers.artifact_list)
    registry.register("export/verification/read", export_handlers.verification_read)
    registry.register("export/release/read", export_handlers.release_read)
    registry.register("export/correction/read", export_handlers.correction_read)
    registry.register("export/audit/read", export_handlers.audit_read)
    registry.register("export/plan/create", export_handlers.plan_create)
    registry.register("export/snapshot/create", export_handlers.snapshot_create)
    registry.register("export/generate", export_handlers.generate)
    registry.register("export/verify", export_handlers.verify)
    registry.register("export/release/prepare", export_handlers.release_prepare)
    registry.register("export/correction/create", export_handlers.correction_create)
