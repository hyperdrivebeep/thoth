"""Compose the trace methods: the requirement-to-result table, its verdicts and its CSV."""

from __future__ import annotations

from thoth.application.commands.trace_origin_admission import with_thread_origins
from thoth.application.commands.verification_trace import TraceHandlers
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.trace_closure import TraceClosures
from thoth.application.services.trace_closure_text import closure_status_cells
from thoth.application.services.verification_trace import VerificationTraceService
from thoth.application.services.verification_trace_import import TraceImportService
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.protocol.registry import MethodRegistry


def register_trace_methods(
    registry: MethodRegistry, records: RequestRecords, projects: ProjectStorePort
) -> None:
    service = VerificationTraceService(records)
    closures = TraceClosures(records)
    handlers = TraceHandlers(
        service=service,
        importer=TraceImportService(service),
        projects=projects,
        closure_notes=lambda project_id: closure_status_cells(
            closures.view(project_id, with_current=False)
        ),
    )
    registry.register("trace/read", handlers.read)
    registry.register("trace/history", handlers.history)
    registry.register("trace/export", handlers.export)
    registry.register("trace/importPreview", handlers.import_preview)
    registry.register("trace/importApply", handlers.import_apply)
    registry.register("trace/confirm", handlers.confirm)


def install_trace_origin(
    registry: MethodRegistry, records: RequestRecords, operations: OperationStorePort
) -> None:
    """thread/list tells which trace row each thread started from. The origin of a research
    request itself is admitted inside the research handlers (trace_origin_admission)."""
    registry.decorate("thread/list", lambda inner: with_thread_origins(inner, records, operations))
