"""Opaque, scope-bound scan cursor. Authority is always rechecked on each read."""

from pydantic import Field, ValidationError

from thoth.application.services.research_history_scope import actor_scope_digest
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.research_history import HistoryTimelineInput
from thoth.domain.restore import RestoreError
from thoth.ports.research_history import HistoryCursorCodecPort, HistoryWatermark


class HistoryCursor(DomainModel):
    version: int = 1
    scope_digest: str
    revisions: int = Field(ge=0)
    memories: int = Field(ge=0)
    after: tuple[str, str, str] | None = None

    @property
    def watermark(self) -> HistoryWatermark:
        return HistoryWatermark(self.revisions, self.memories)


def cursor_scope(request: HistoryTimelineInput) -> str:
    return domain_digest(
        "HISTORY_QUERY",
        "1.0.0",
        canonical_payload(
            {
                "scope": request.scope,
                "kinds": request.kinds,
                "actor_scope_digest": actor_scope_digest(request.project_id),
            }
        ),
    )


def decode_cursor(
    request: HistoryTimelineInput, codec: HistoryCursorCodecPort
) -> HistoryCursor | None:
    if request.cursor is None:
        return None
    try:
        if len(request.cursor) > 4096:
            raise ValueError("oversized cursor")
        result = HistoryCursor.model_validate_json(codec.decode(request.cursor))
        if result.version != 1 or result.scope_digest != cursor_scope(request):
            raise ValueError("cursor scope")
        return result
    except (ValueError, ValidationError) as exc:
        raise RestoreError("HISTORY_CURSOR_INVALID") from exc
