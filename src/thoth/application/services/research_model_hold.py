"""A model call that ended the research early is reported as a model-call failure when read."""

from __future__ import annotations

from pydantic import JsonValue

from thoth.domain.research_request import (
    CurrentResultManifest,
    CurrentResultManifestV21,
    ResearchAttempt,
)

_COMPLETE = "BOUNDED_RESEARCH_COMPLETE"


def model_call_hold_failure(
    manifest: CurrentResultManifest | CurrentResultManifestV21 | None,
    attempt: ResearchAttempt | None,
) -> dict[str, JsonValue] | None:
    """Failure view for a HOLD result that the research runner published after a model call stopped.

    The operation stays SUCCEEDED with its HOLD result; this only makes the cause readable. A HOLD
    result that has an answer (an ordinary evidence hold) is not a failure.
    """
    if manifest is None or manifest.phase != "HOLD":
        return None
    reason = manifest.terminal_reason
    if not reason or reason == _COMPLETE:
        return None
    answer = manifest.result.get("answer")
    if isinstance(answer, str) and answer.strip():
        return None
    return {
        "primary": {
            "reason_code": reason,
            "exception_type": "ModelExecutionHold",
            "origin": "MODEL_CALL",
            "detail": None,
        },
        "secondary": None,
        "remote_observation": "UNKNOWN" if attempt is None else attempt.remote_observation,
        "retry_requires_user_action": True,
    }
