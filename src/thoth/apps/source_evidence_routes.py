"""Register source and evidence RPCs against their existing handler instances."""

from thoth.application.commands import EvidenceCommandHandlers
from thoth.application.commands.sources import SourceCommandHandlers
from thoth.protocol.registry import MethodRegistry


def register_source_and_evidence_methods(
    registry: MethodRegistry,
    source_handlers: SourceCommandHandlers,
    evidence_handlers: EvidenceCommandHandlers,
) -> None:
    registry.register("project/source/connect", source_handlers.connect)
    registry.register("project/source/disconnect", source_handlers.disconnect)
    registry.register("project/source/list", source_handlers.list_sources)
    registry.register("project/source/time/confirm", source_handlers.confirm_time)
    registry.register("project/source/time/correct", source_handlers.correct_time)
    registry.register("evidence/list", evidence_handlers.list)
    registry.register("evidence/read", evidence_handlers.read)
    registry.register("evidence/packet/read", evidence_handlers.packet_read)
    registry.register("evidence/conflict/list", evidence_handlers.conflict_list)
    registry.register("evidence/conflict/read", evidence_handlers.conflict_read)
    registry.register("evidence/audit/read", evidence_handlers.audit_read)
    registry.register("evidence/source/add", evidence_handlers.source_add)
    registry.register("evidence/source/refresh", evidence_handlers.source_refresh)
    registry.register("evidence/source/metadata/correct", evidence_handlers.source_metadata_correct)
    registry.register("evidence/span/correct", evidence_handlers.span_correct)
    registry.register("evidence/link/propose", evidence_handlers.link_propose)
    registry.register("evidence/link/correct", evidence_handlers.link_correct)
    registry.register("evidence/challenge", evidence_handlers.challenge)
    registry.register("evidence/revalidate", evidence_handlers.revalidate)
