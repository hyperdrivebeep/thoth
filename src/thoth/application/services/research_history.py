"""Read-only history orchestration with bounded scans and exact result binding."""

from thoth.application.services.history_associations import result_scope_links
from thoth.application.services.history_cursor import HistoryCursor, cursor_scope, decode_cursor
from thoth.application.services.history_projection import HistoryProjection
from thoth.application.services.research_history_scope import (
    HistoryScopeValidator,
    actor_scope_digest,
)
from thoth.application.services.resource_scope_read_context import scope_read_transaction
from thoth.domain.research_history import (
    HistoryCoverage,
    HistoryDetail,
    HistoryItem,
    HistoryItemInput,
    HistoryPage,
    HistoryScope,
    HistoryTimelineInput,
)
from thoth.domain.restore import RestoreError
from thoth.ports.research_history import HistoryCursorCodecPort, HistoryRow


class ResearchHistoryService:
    def __init__(
        self,
        projection: HistoryProjection,
        scopes: HistoryScopeValidator,
        cursors: HistoryCursorCodecPort,
    ) -> None:
        self.projection, self.scopes = projection, scopes
        self.cursors = cursors
        self.scan_limit = 200

    def list_timeline(self, request: HistoryTimelineInput) -> HistoryPage:
        self.scopes.require(request.project_id, request.scope)
        with self.projection.ledger.transaction(), scope_read_transaction():
            cursor = decode_cursor(request, self.cursors)
            watermark = (
                self.projection.reader.watermark(request.project_id)
                if cursor is None
                else cursor.watermark
            )
            after = None if cursor is None else cursor.after
            associations, links_limited = result_scope_links(
                self.projection.ledger,
                self.projection.reader,
                self.projection.access,
                request.project_id,
                watermark,
            )
            rows = self.projection.reader.page(
                request.project_id, watermark, after, self.scan_limit + 1
            )
            items: list[HistoryItem] = []
            partial = False
            scanned = 0
            for row in rows[: self.scan_limit]:
                after, scanned = row.sort_key, scanned + 1
                try:
                    projected = self.projection.read(request.project_id, row)
                except RestoreError as exc:
                    if exc.reason_code not in {
                        "HISTORY_SCHEMA_UNSUPPORTED",
                        "HISTORY_RECORD_UNAVAILABLE",
                    }:
                        raise
                    partial = True
                    continue
                if projected is None:
                    continue
                item, _ = projected
                if item.record_ref.revision_digest in associations:
                    links = associations[item.record_ref.revision_digest]
                    item = item.model_copy(
                        update={
                            "scope_links": links,
                            "association": "PRODUCED_IN"
                            if any(link.association == "PRODUCED_IN" for link in links)
                            else "USED_BY",
                        }
                    )
                partial = partial or item.association == "UNATTRIBUTED"
                if request.kinds and item.kind not in request.kinds:
                    continue
                if not self._in_scope(request.scope, item):
                    continue
                items.append(item)
                if len(items) == request.limit:
                    break
            more = scanned < len(rows)
            next_cursor = (
                self.cursors.encode(
                    HistoryCursor(
                        scope_digest=cursor_scope(request),
                        revisions=watermark.revisions,
                        memories=watermark.memories,
                        after=after,
                    ).model_dump_json()
                )
                if more
                else None
            )
            return HistoryPage(
                items=tuple(items),
                next_cursor=next_cursor,
                actor_scope_digest=actor_scope_digest(request.project_id),
                coverage=HistoryCoverage(
                    association="PARTIAL" if partial or links_limited else "EXACT",
                    scan="LIMITED"
                    if more and scanned == self.scan_limit
                    else "CONTINUATION"
                    if more
                    else "COMPLETE_PAGE",
                    reasons=("UNATTRIBUTED_RECORDS",) if partial else (),
                ),
            )

    def read_item(self, request: HistoryItemInput) -> HistoryDetail:
        if request.record_ref.project_id != request.project_id:
            raise RestoreError("HISTORY_SCOPE_MISMATCH")
        self.scopes.require(request.project_id, request.scope)
        with self.projection.ledger.transaction(), scope_read_transaction():
            row = HistoryRow(
                request.record_ref.owner_kind,
                request.record_ref.immutable_id,
                request.record_ref.revision_digest,
                "",
            )
            projected = self.projection.read(request.project_id, row)
            if projected is None:
                raise RestoreError("HISTORY_RECORD_UNAVAILABLE")
            item, content = projected
            associations, _ = result_scope_links(
                self.projection.ledger,
                self.projection.reader,
                self.projection.access,
                request.project_id,
                self.projection.reader.watermark(request.project_id),
            )
            if item.record_ref.revision_digest in associations:
                links = associations[item.record_ref.revision_digest]
                item = item.model_copy(
                    update={
                        "scope_links": links,
                        "association": "PRODUCED_IN"
                        if any(link.association == "PRODUCED_IN" for link in links)
                        else "USED_BY",
                    }
                )
            if not self._in_scope(request.scope, item):
                raise RestoreError("HISTORY_SCOPE_MISMATCH")
            current = self.projection.ledger.read_heads(request.project_id).get(
                f"{item.entity_type}:{item.entity_id}"
            )
            if current and not self.projection.access.may_read_revision(
                request.project_id, current
            ):
                current = None
            return HistoryDetail(
                item=item,
                content=content,
                current_head_digest=current,
                coverage=HistoryCoverage(
                    association="PARTIAL" if item.association == "UNATTRIBUTED" else "EXACT"
                ),
            )

    @staticmethod
    def _in_scope(scope: HistoryScope, item: HistoryItem) -> bool:
        if scope.entity_ref and scope.entity_ref != f"{item.entity_type}:{item.entity_id}":
            return False
        if scope.thread_id or scope.request_revision_digest:
            return any(
                (scope.thread_id is None or link.thread_id == scope.thread_id)
                and (
                    scope.request_revision_digest is None
                    or link.request_revision_digest == scope.request_revision_digest
                )
                for link in item.scope_links
            )
        return True
