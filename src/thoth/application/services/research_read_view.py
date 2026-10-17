"""Assemble scoped followup/activity projections for a requested thread view."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

if TYPE_CHECKING:
    from thoth.application.services.research_thread_read_snapshot import ResearchThreadReadSnapshot
    from thoth.ports.ledger import LedgerPort
    from thoth.ports.resource_scope import ResourceAccessPort


def project_thread_read_view(
    *,
    value: dict[str, JsonValue],
    payload: dict[str, object],
    snapshot: ResearchThreadReadSnapshot,
    effective: str,
    ledger: LedgerPort,
    access: ResourceAccessPort,
) -> dict[str, JsonValue]:
    from thoth.application.services.research_followup_projection import project_thread_followup
    from thoth.application.services.research_progress_view import project_activity_events
    from thoth.application.services.research_read_summary import (
        progress_thread_read,
        summarize_thread_read,
    )
    from thoth.application.services.user_activity_projection import project_user_activity_events

    current_ref, request, attempt = snapshot.current_ref, snapshot.request, snapshot.attempt
    result_ref, manifest = snapshot.result_ref, snapshot.manifest
    currentness = snapshot.currentness
    activity_source_context = snapshot.activity_source_context
    if value.get("view") != "PROGRESS":
        progress_summary, coverage_matrix, next_user_action = project_thread_followup(
            ledger=ledger,
            access=access,
            request_ref=current_ref,
            request=request,
            result_ref=result_ref,
            manifest=manifest,
            attempt=attempt,
            operation_state=effective,
            currentness_state=currentness.model_dump(mode="json"),
        )
        payload["user_progress_summary"] = progress_summary.model_dump(mode="json")
        payload["coverage_matrix"] = coverage_matrix.model_dump(mode="json")
        payload["next_user_action"] = next_user_action.model_dump(mode="json")
    payload["activity_events"] = project_activity_events(cast(dict[str, JsonValue], payload))
    payload["user_activity_events"] = project_user_activity_events(
        payload, source_context=activity_source_context
    )
    shown = cast(dict[str, JsonValue], payload)
    if value.get("view", "SUMMARY") == "FULL":
        return {**shown, "view": "FULL"}
    if value.get("view") == "PROGRESS":
        return progress_thread_read(shown)
    return summarize_thread_read(shown)
