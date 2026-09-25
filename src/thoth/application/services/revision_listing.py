"""Traverse readable revision history without turning a hidden ancestor into a global failure."""

from pydantic import JsonValue

from thoth.domain.canonical import head_set_digest
from thoth.domain.enums import EntityType
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.revision import SemanticRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_history import ResearchHistoryReadPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def visible_revisions(ledger: LedgerPort, project_id: str) -> tuple[SemanticRevision, ...]:
    values: dict[str, SemanticRevision] = {}
    seen: set[str] = set()
    frontier = list(ledger.read_heads(project_id).values())
    while frontier:
        digest = str(frontier.pop())
        if digest in seen:
            continue
        seen.add(digest)
        try:
            revision = ledger.read_revision_by_digest(project_id, digest)
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                continue
            raise
        if revision is not None:
            values[digest] = revision
            frontier.extend(revision.parent_revision_digests)
    return tuple(sorted(values.values(), key=lambda item: (item.created_at, item.revision_id)))


def history_revisions(
    ledger: LedgerPort, project: str, reader: ResearchHistoryReadPort | None
) -> tuple[SemanticRevision, ...]:
    if reader is None:
        return visible_revisions(ledger, project)
    rows = reader.page(project, reader.watermark(project), None, 2001)
    if len(rows) > 2000:
        raise RpcApplicationError(
            RpcErrorCode.DOMAIN_REJECTED,
            "Use paginated research history",
            data={"reason_code": "HISTORY_SCAN_LIMIT"},
        )
    visible: list[SemanticRevision] = []
    for row in rows:
        if row.owner_kind != "SEMANTIC_REVISION":
            continue
        try:
            revision = ledger.read_revision_by_digest(project, row.revision_digest)
        except ResourceScopeError:
            continue
        if revision is not None:
            visible.append(revision)
    return tuple(visible)


def revision_list_view(
    ledger: LedgerPort,
    revisions: tuple[SemanticRevision, ...],
    project: str,
    entity_type: EntityType | None,
    entity_id: str | None,
    commit_state: str | None,
    freshness: str | None,
) -> dict[str, JsonValue]:
    heads = set(ledger.read_heads(project).values())
    states = ledger.read_dependency_states(project)
    rows: list[JsonValue] = []
    for item in revisions:
        if entity_type is not None and item.entity_type != entity_type:
            continue
        if entity_id is not None and item.entity_id != entity_id:
            continue
        key = f"{item.entity_type.value}:{item.entity_id}"
        state = (
            states[key].value
            if item.revision_digest in heads and key in states
            else ("CURRENT" if item.revision_digest in heads else "SUPERSEDED")
        )
        if commit_state not in {None, "COMMITTED"} or freshness not in {None, state}:
            continue
        rows.append(
            {
                **item.model_dump(mode="json"),
                "commit_state": "COMMITTED",
                "freshness": state,
                "head_member": item.revision_digest in heads,
            }
        )
    return {"revisions": rows, "next_cursor": None}


def public_head_view(
    ledger: LedgerPort, project: str, aggregate_ids: tuple[str, ...]
) -> dict[str, JsonValue]:
    actual = dict(ledger.read_heads(project))
    visible: dict[str, JsonValue] = {}
    for key, digest in actual.items():
        if aggregate_ids and key.split(":", 1)[-1] not in aggregate_ids:
            continue
        try:
            revision = ledger.read_revision_by_digest(project, digest)
        except ResourceScopeError:
            continue
        if revision is not None:
            visible[key] = digest
    # The public subset never replaces the complete internal CAS map.
    return {
        "working_heads": visible,
        "project_head_set_digest": head_set_digest(actual),
        "visibility": "AUTHORIZED_SUBSET",
    }
