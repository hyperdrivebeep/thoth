"""Read accepted-input activity alongside the legacy thread activity stream."""

from collections.abc import Awaitable, Callable
from typing import cast

from pydantic import JsonValue

from thoth.application.services.request_records import RequestRecords
from thoth.domain.research_request import InputDelivery, decode_thread_request
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


async def read_conversation_activity(
    value: dict[str, JsonValue],
    *,
    records: RequestRecords,
    access: ResourceAccessPort,
    legacy_activity_list: Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]],
) -> dict[str, JsonValue]:
    from thoth.application.services.conversation_inputs import conversation_inputs

    before = value.get("conversation_before_epoch")
    if before is not None and (type(before) is not int or before < 1):
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "invalid conversation cursor")
    base = await legacy_activity_list(
        {k: v for k, v in value.items() if k != "conversation_before_epoch"}
    )
    project, thread = str(value["project_id"]), str(value["thread_id"])
    activities = cast(list[JsonValue], base["activities"])
    for revision in records.ledger.read_revisions(project, "THREAD", f"request:{thread}"):
        if not access.may_read_revision(project, revision.revision_digest):
            continue
        snapshot = records.ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            continue
        request = decode_thread_request(snapshot.content)
        delivery = records.journal_read(
            project, f"input:{request.accepted_input_ids[-1]}", InputDelivery
        )
        activities.append(
            {
                "activity_id": revision.revision_id,
                "event_type": "research/inputAccepted",
                "created_at": revision.created_at.isoformat(),
                "actor_id": request.actor_ref,
                "payload": {
                    "request_epoch": request.request_epoch,
                    "authored_text_ref": request.authored_text_ref.model_dump(mode="json"),
                    "input_state": None if delivery is None else delivery.state,
                },
            }
        )
    return {
        "conversation": conversation_inputs(records, access, project, thread, before),
        "activities": sorted(
            activities,
            key=lambda item: str(item.get("created_at", "") if isinstance(item, dict) else ""),
        ),
    }
