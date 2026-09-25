"""Create reference-only memory candidates from owned semantic revisions."""

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType, MemoryKind, MemoryPayloadMode, RecallEligibility
from thoth.domain.memory import MemoryRecord
from thoth.domain.revision import SemanticRevision


def domain_reference_memory(revision: SemanticRevision, memory_id: str) -> MemoryRecord | None:
    kinds = {
        EntityType.HYPOTHESIS: MemoryKind.HYPOTHESIS,
        EntityType.ACTION: MemoryKind.ACTION,
        EntityType.OUTCOME: MemoryKind.FACT,
    }
    if revision.entity_type not in kinds:
        return None
    draft = {
        "memory_id": memory_id,
        "project_id": revision.project_id,
        "payload_mode": MemoryPayloadMode.DOMAIN_REFERENCE.value,
        "kind": kinds[revision.entity_type].value,
        "owner_revision_ref": revision.revision_digest,
        "source_ref": f"{revision.entity_type.value}:{revision.entity_id}",
        "recall_eligibility": RecallEligibility.WORKING_CONTEXT.value,
    }
    return MemoryRecord.model_validate(
        {
            **draft,
            "revision_digest": domain_digest("MEMORY_RECORD", "1.0.0", canonical_payload(draft)),
        }
    )
