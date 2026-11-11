"""What a result read says about how a screen shows its result.

thread/result/read returns the stored manifest unchanged and a result that may be a read view of it
(expected_result_view, for a result stored before the grounded-row contract). A screen that shows
the summary view of a result (thread/read) has to be able to tell that what it shows is this
stored answer. shown_result is the same summary view, made by the same function, of the result this
read returns; projected_keys names the top-level keys the read view changed, so that the screen can
require every other key to be identical to the stored manifest.
"""

from pydantic import JsonValue

from thoth.application.services.research_read_summary import summarize_result

# research_threads publication adds these to operation.result only; they are not part of the
# stored manifest result and are not a projection.
ENVELOPE_KEYS = frozenset({"thread_id", "request_epoch", "contract_version", "terminal_reason"})


def projected_keys(result: dict[str, JsonValue], stored: dict[str, JsonValue]) -> list[str]:
    keys = (set(result) | set(stored)) - ENVELOPE_KEYS
    return sorted(k for k in keys if result.get(k) != stored.get(k))


def with_shown_result(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    manifest = payload.get("manifest")
    stored = manifest.get("result") if isinstance(manifest, dict) else None
    keys: list[JsonValue] | None
    if payload.get("expected_result_view") is None:
        keys = []
    elif isinstance(stored, dict):
        keys = list(projected_keys(result, stored))
    else:
        keys = None  # a read view without the manifest to compare with: not known
    return {**payload, "shown_result": summarize_result(result), "projected_keys": keys}
