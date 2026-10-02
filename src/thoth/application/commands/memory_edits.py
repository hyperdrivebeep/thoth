"""memory/edit/propose: a user corrects one stored memory; the ordinary review judges it."""

from __future__ import annotations

from typing import Annotated, cast

from pydantic import Field, JsonValue, StringConstraints

from thoth.application.commands.memory_revisions import MemoryRevisionHandlers
from thoth.application.services.memory_edit_service import MemoryEditService
from thoth.domain.base import DomainModel

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
EvidenceRef = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class MemoryEditInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    target_revision_digest: str = Field(min_length=64, max_length=64)
    corrected_text: Text
    reason: Reason
    evidence_refs: tuple[EvidenceRef, ...] = Field(default=(), max_length=20)


class MemoryEditHandlers:
    def __init__(self, *, service: MemoryEditService, rows: MemoryRevisionHandlers) -> None:
        self._service, self._rows = service, rows

    async def propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MemoryEditInput.model_validate(value)
        result = await self._service.propose(
            project_id=request.project_id,
            target_revision_digest=request.target_revision_digest,
            corrected_text=request.corrected_text,
            reason=request.reason,
            evidence_refs=tuple(dict.fromkeys(request.evidence_refs)),
        )
        revision = result.revision
        return {
            "edit_id": result.edit_id,
            "target_revision_digest": result.target_revision_digest,
            "revision": self._rows.describe(request.project_id, revision),
            "transition": revision.transition.value,
            "reviews": cast(
                list[JsonValue],
                [
                    {
                        "role": review.role.value,
                        "verdict": review.verdict.value,
                        "reason_code": review.reason_code,
                        "scripted": review.scripted is True,
                    }
                    for review in revision.reviews
                ],
            ),
        }
