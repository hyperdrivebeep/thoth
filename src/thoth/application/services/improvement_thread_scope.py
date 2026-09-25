"""Preserve stored Thread scope when Improvement records refer to that Thread."""

from thoth.domain.auth import authenticated_data_scope_allows
from thoth.domain.control_record import ControlRecord
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.thread import ThreadStorePort


class ImprovementThreadScope:
    def __init__(self, records: ControlRecordStorePort, threads: ThreadStorePort) -> None:
        self._records, self._threads = records, threads

    def require_thread(self, project_id: str, thread_id: object) -> None:
        if thread_id is None:
            return
        if not isinstance(thread_id, str) or not thread_id:
            raise ResourceScopeError("EVALUATION_THREAD_INVALID")
        thread = self._threads.read(thread_id)
        if thread is None or thread.project_id != project_id:
            raise ResourceScopeError("EVALUATION_THREAD_NOT_FOUND")
        if not authenticated_data_scope_allows(thread.scope):
            raise ResourceScopeError("AUTH_DATA_SCOPE_DENIED")

    def require_record(self, record: ControlRecord) -> None:
        pending = [record]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current.record_digest in seen:
                continue
            if len(seen) >= 16:
                raise ResourceScopeError("EVALUATION_THREAD_LINEAGE_LIMIT")
            seen.add(current.record_digest)
            self.require_thread(current.project_id, current.payload.get("thread_id"))
            for key in ("improvement_revision_id", "evaluation_plan_id", "promotion_id"):
                reference = current.payload.get(key)
                if isinstance(reference, str):
                    parent = self._records.read(current.project_id, "IMPROVEMENT", reference)
                    if parent is not None:
                        pending.append(parent)

    def may_read(self, record: ControlRecord) -> bool:
        try:
            self.require_record(record)
        except ResourceScopeError:
            return False
        return True
