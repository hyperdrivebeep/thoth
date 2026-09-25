import inspect

from thoth.application.commands.evidence import EvidenceCommandHandlers
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.apps.runtime import AppRuntime


def evidence_service(runtime: AppRuntime) -> EvidenceGraphService:
    handler = runtime.bus._registry.resolve("evidence/link/propose")  # pyright: ignore[reportPrivateUsage]
    assert inspect.ismethod(handler) and isinstance(handler.__self__, EvidenceCommandHandlers)
    return handler.__self__._service  # pyright: ignore[reportPrivateUsage]
