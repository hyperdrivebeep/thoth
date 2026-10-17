"""Pure field projections over an already-authorized thread read snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from thoth.application.services.research_model_hold import model_call_hold_failure

if TYPE_CHECKING:
    from thoth.application.services.research_thread_read_snapshot import ResearchThreadReadSnapshot
    from thoth.domain.model_dispatch import ModelDispatchRecord
    from thoth.domain.research_failure import ResearchCleanupFailure, ResearchFailureRecord


def snapshot_fields(
    snapshot: ResearchThreadReadSnapshot,
    base: dict[str, JsonValue],
    completion: dict[str, object],
    failure: ResearchFailureRecord | None,
    cleanup_failure: ResearchCleanupFailure | None,
) -> dict[str, object]:
    current_ref, request, attempt = snapshot.current_ref, snapshot.request, snapshot.attempt
    manifest, currentness, fresh = snapshot.manifest, snapshot.currentness, snapshot.fresh
    op = snapshot.operation
    effective = "UNKNOWN" if op is None else op.state.value
    inconsistent = (
        attempt is not None and effective in {"FAILED", "CANCELLED"} and attempt.status != effective
    )
    return {
        **base,
        **completion,
        "contract_version": 2,
        "request_ref": current_ref.model_dump(mode="json"),
        "request_epoch": request.request_epoch,
        "operation_state": "UNKNOWN" if op is None else op.state.value,
        "operation_error": None if op is None else op.error,
        "failure": model_call_hold_failure(manifest if fresh else None, attempt)
        if failure is None
        else failure.model_dump(mode="json"),
        "cleanup_failure": None
        if cleanup_failure is None
        else cleanup_failure.model_dump(mode="json"),
        "execution_summary": {
            "effective_execution_state": effective,
            "state_inconsistent": inconsistent,
            "last_checkpoint_only": effective in {"FAILED", "CANCELLED"} and manifest is not None,
            "automatic_retry": False,
        },
        "request": request.model_dump(mode="json"),
        "attempt": None if attempt is None else attempt.model_dump(mode="json"),
        "current_result": manifest.model_dump(mode="json") if fresh and manifest else None,
        "previous_result": manifest.model_dump(mode="json") if manifest and not fresh else None,
        "freshness": "CURRENT" if fresh else "STALE_OR_PENDING_CURRENT_REQUEST",
        "basis_currentness": currentness.model_dump(mode="json"),
        "cursor": None if attempt is None else attempt.event_cursor,
    }


def model_dispatch_fields(
    dispatches: tuple[ModelDispatchRecord, ...],
) -> list[dict[str, JsonValue]]:
    return [
        {
            "dispatch_id": dispatch.dispatch_id,
            "state": dispatch.state,
            "received_bytes": dispatch.received_bytes,
            "remote_stop": dispatch.remote_stop,
            "transport_observation": None
            if dispatch.transport_observation is None
            else dispatch.transport_observation.model_dump(mode="json"),
        }
        for dispatch in dispatches
    ]
