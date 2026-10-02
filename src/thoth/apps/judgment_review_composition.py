"""Assemble the judgment re-examination RPCs and connect them to the research loop."""

from __future__ import annotations

from thoth.application.commands.judgment_review import JudgmentReviewHandlers
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.judgment_review_service import JudgmentReviewService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry


class JudgmentReviewWiring:
    """Registered handlers waiting for the bus, which is built after the registry."""

    def __init__(self, handlers: JudgmentReviewHandlers, service: JudgmentReviewService) -> None:
        self._handlers, self._service = handlers, service

    def connect(self, bus: CommandBus, research: ResearchThreadHandlers) -> None:
        """Send instructions through the bus and resolve requests when a result is published."""

        self._handlers.bind_dispatcher(BusConversationDispatcher(bus))
        research.on_result_published = self._service.resolve_for_operation


def install_judgment_review(
    registry: MethodRegistry,
    stores: StoreBundlePort,
    controls: ControlRecordService,
    hypotheses: HypothesisStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> JudgmentReviewWiring:
    service = JudgmentReviewService(
        controls=controls,
        store=stores.controls,
        hypotheses=hypotheses,
        threads=stores.threads,
        operations=stores.operations,
        clock=clock,
        ids=ids,
    )
    handlers = JudgmentReviewHandlers(service)
    registry.register("hypothesis/review/request", handlers.request)
    registry.register("hypothesis/review/list", handlers.list)
    return JudgmentReviewWiring(handlers, service)
