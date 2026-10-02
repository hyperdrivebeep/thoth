"""The first-screen view of a thread read: what a screen shows, without the stored internals.

The omitted names are values no screen reads. They stay in the stored result and in the full read
(view FULL) and the result read, so nothing is lost; they are only not sent with every status poll.
"""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

VIEWS = frozenset({"SUMMARY", "FULL", "PROGRESS"})

# Result manifest fields that hold the evidence basis and resource records behind a result.
OMITTED_MANIFEST_FIELDS = ("research_basis", "resource_uses", "source_basis", "record_refs")

# Result keys that are execution internals or full copies of data served elsewhere.
OMITTED_RESULT_KEYS = frozenset(
    {
        "action_compilation",
        "authorized_project_memory",
        "behavior_execution",
        "context_bundle",
        "coverage",
        "criterion_profile_decision",
        "full_memory",
        "full_project_memory",
        "full_project_memory_context",
        "gap_targets",
        "internal_expansion_waves",
        "multi_baseline",
        "requirements",
        "source_overview",
        "source_packet",
    }
)


# Running-state logs that nothing shows once the operation has ended; the full read keeps them.
OMITTED_WHEN_FINISHED = ("activity_events", "user_activity_events")
TERMINAL_OPERATION_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


def progress_thread_read(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """The running-state parts only, for the panel that polls while research is running."""
    progress = {k: v for k, v in payload.items() if k not in ("current_result", "previous_result")}
    progress["view"] = "PROGRESS"
    return progress


def summarize_thread_read(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    summary = dict(payload)
    for key in ("current_result", "previous_result"):
        manifest = summary.get(key)
        if not isinstance(manifest, dict):
            continue
        slim = {k: v for k, v in manifest.items() if k not in OMITTED_MANIFEST_FIELDS}
        result = slim.get("result")
        if isinstance(result, dict):
            slim["result"] = {k: v for k, v in result.items() if k not in OMITTED_RESULT_KEYS}
        summary[key] = cast(JsonValue, slim)
    if summary.get("operation_state") in TERMINAL_OPERATION_STATES:
        # The activity log only drives the panel shown while research runs.
        for name in OMITTED_WHEN_FINISHED:
            summary.pop(name, None)
    summary["view"] = "SUMMARY"
    return summary
