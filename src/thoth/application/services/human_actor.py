"""Who may write a judgment record: a person. A model, an agent or a system actor may not."""

from __future__ import annotations

NOT_HUMAN_PREFIXES = ("system:", "agent:", "model:", "bot:")
RECORD_HUMAN_ONLY = "RECORD_HUMAN_ONLY"


def require_human_actor(actor_id: str) -> None:
    if not actor_id or actor_id.startswith(NOT_HUMAN_PREFIXES):
        raise PermissionError(RECORD_HUMAN_ONLY)
