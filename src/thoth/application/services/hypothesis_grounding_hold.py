"""Stable publication HOLD for server provenance failures, separate from row rejections."""

from thoth.domain.research_execution import ResearchWork
from thoth.ports.model import ModelExecutionHold

GROUNDING_PUBLICATION_HOLD = "GROUNDING_PUBLICATION_BINDING_MISMATCH"


class GroundingPublicationHold(ModelExecutionHold):
    def __init__(self, cause_code: str) -> None:
        super().__init__(GROUNDING_PUBLICATION_HOLD)
        self.cause_code = cause_code


def is_capture_failure(error: BaseException) -> bool:
    return isinstance(error, ValueError) and (
        str(error).startswith(("GROUNDING_STAGE_", "GROUNDING_SUPPLIED_"))
        or str(error)
        in {
            "GROUNDING_PROVIDER_CONTRACT_MISMATCH",
            "GROUNDING_CAPTURE_SCOPE_MISSING",
            "GROUNDING_SOURCE_STAGE_MISSING",
        }
    )


def model_hold_payload(work: ResearchWork, reason: str) -> dict[str, object]:
    payload = {**work.context, **work.result_only}
    if reason == GROUNDING_PUBLICATION_HOLD:
        # These candidate refs may have been staged but were not committed by the failed cycle.
        for key in ("hypothesis_revision_refs", "hypothesis_review", "hypothesis_table_fill"):
            payload.pop(key, None)
        payload["research_state"] = "PARTIAL"
    return payload
