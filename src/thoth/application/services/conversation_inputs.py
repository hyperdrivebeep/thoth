"""Read-only conversation input projection from the canonical request parent chain.

Results are fetched through the existing operation/result/read authority boundary.
No new conversation owner, synthetic answer, or mutable cache is introduced.
"""

from pydantic import JsonValue

from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.research_request import ThreadRequestRevision
from thoth.ports.resource_scope import ResourceAccessPort


def conversation_inputs(
    records: RequestRecords,
    access: ResourceAccessPort,
    project: str,
    thread: str,
    before_epoch: int | None,
) -> dict[str, JsonValue]:
    current = records.read(project, EntityType.THREAD, f"request:{thread}")
    if current is None:
        return {"turns": [], "next_before_epoch": None, "history_limited": False}
    revisions = {
        r.revision_digest: r
        for r in records.ledger.read_revisions(project, "THREAD", f"request:{thread}")
    }
    digest: str | None = current[0].revision_digest
    seen: set[str] = set()
    turns: list[JsonValue] = []
    epochs: list[int] = []
    while digest is not None and len(seen) < 2000:
        if digest in seen or digest not in revisions:
            raise ValueError("CONVERSATION_REQUEST_LINEAGE_INVALID")
        seen.add(digest)
        revision = revisions[digest]
        snapshot = records.ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            raise ValueError("CONVERSATION_REQUEST_SNAPSHOT_MISSING")
        request = ThreadRequestRevision.model_validate(snapshot.content)
        if request.project_id != project or request.thread_id != thread:
            raise ValueError("CONVERSATION_REQUEST_SCOPE_MISMATCH")
        if request.parent_ref is not None and (
            request.parent_ref.project_id != project
            or request.parent_ref.entity_id != f"request:{thread}"
            or request.parent_ref.entity_type != "THREAD"
        ):
            raise ValueError("CONVERSATION_PARENT_SCOPE_MISMATCH")
        digest = None if request.parent_ref is None else request.parent_ref.revision_digest
        if before_epoch is not None and request.request_epoch >= before_epoch:
            continue
        if not access.may_read_revision(
            project, revision.revision_digest
        ) or not access.may_read_revision(project, request.authored_text_ref.revision_digest):
            continue
        if len(turns) == 25:
            return {
                "turns": list(reversed(turns)),
                "next_before_epoch": epochs[-1],
                "history_limited": False,
            }
        turns.append(
            {
                "request_epoch": request.request_epoch,
                "request_revision_digest": revision.revision_digest,
                "authored_text_ref": request.authored_text_ref.model_dump(mode="json"),
                "operation_id": request.operation_id,
                "text": request.authored_text,
                "edit_kind": request.edit_kind,
                "created_at": revision.created_at.isoformat(),
            }
        )
        epochs.append(request.request_epoch)
    return {
        "turns": list(reversed(turns)),
        "next_before_epoch": None,
        "history_limited": digest is not None,
    }
