"""Deterministic history items from exact immutable owners, never guessed episodes."""

from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.domain.research_history import (
    HistoryCapability,
    HistoryItem,
    HistoryRecordRef,
    HistoryScopeLink,
)
from thoth.domain.research_request import ThreadRequestRevision
from thoth.domain.restore import RestoreError
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_history import HistoryRow, ResearchHistoryReadPort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.restore import RestoreProfileRegistryPort


class HistoryProjection:
    def __init__(
        self,
        ledger: LedgerPort,
        reader: ResearchHistoryReadPort,
        access: ResourceAccessPort,
        freshness: ResearchFreshnessService,
        profiles: RestoreProfileRegistryPort,
        apply_ready: bool = False,
    ) -> None:
        self.ledger, self.reader, self.access, self.freshness = ledger, reader, access, freshness
        self.profiles, self.apply_ready = profiles, apply_ready

    def read(self, project: str, row: HistoryRow) -> tuple[HistoryItem, dict[str, object]] | None:
        ref = HistoryRecordRef(
            owner_kind=row.owner_kind,
            project_id=project,
            immutable_id=row.immutable_id,
            revision_digest=row.revision_digest,
        )
        if row.owner_kind == "FULL_MEMORY":
            memory = self.reader.read_memory(project, row.revision_digest)
            if memory is None or not self.access.may_read_revision(
                project, memory.owner_revision_ref
            ):
                return None
            if memory.memory_revision_id != row.immutable_id:
                raise RestoreError("HISTORY_RECORD_MISMATCH")
            if any(not self.access.may_read(project, ref) for ref in memory.evidence_refs):
                return None
            return HistoryItem(
                item_id=f"FULL_MEMORY:{memory.memory_revision_id}",
                kind="MEMORY",
                occurred_at=memory.created_at,
                record_ref=ref,
                association="UNATTRIBUTED",
                head_membership="UNKNOWN",
                title="연구 기억 기록",
                schema_family="FullMemoryRevision",
                currentness=self.freshness.owner_eligibility(project, memory.owner_revision_ref),
            ), memory.model_dump(mode="json")
        if not self.access.may_read_revision(project, row.revision_digest):
            return None
        revision = self.ledger.read_revision_by_digest(project, row.revision_digest)
        snapshot = None if revision is None else self.ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise RestoreError("HISTORY_RECORD_UNAVAILABLE")
        if revision.revision_id != row.immutable_id:
            raise RestoreError("HISTORY_RECORD_MISMATCH")
        content = dict(snapshot.content)
        family = str(content.get("record_kind", "SemanticRevision"))
        key = f"{revision.entity_type.value}:{revision.entity_id}"
        currentness = self.freshness.evaluate_entity(project, key, revision.revision_digest)
        links: tuple[HistoryScopeLink, ...] = ()
        operation = None
        origin = None
        details: dict[str, object] = {}
        kind = "REVISION"
        title = "연구 항목 변경"
        capability = HistoryCapability()
        if "record_kind" not in content:
            try:
                profile, record = self.profiles.resolve(revision, snapshot)
                family, title = type(record).__name__, f"{profile.display_name} 변경"
                capability = HistoryCapability(
                    restore="RESTORE_SUPPORTED" if self.apply_ready else "READ_ONLY",
                    preview_supported=True,
                    apply_ready=self.apply_ready,
                    reason_codes=() if self.apply_ready else ("RESTORE_NOT_READY",),
                )
            except RestoreError:
                pass
        if family == "ThreadRequestRevision":
            request = ThreadRequestRevision.model_validate(content)
            if request.project_id != project or key != f"THREAD:request:{request.thread_id}":
                raise RestoreError("HISTORY_SCOPE_MISMATCH")
            if not self.access.may_read_revision(
                project, request.authored_text_ref.revision_digest
            ):
                return None
            kind, title, operation, origin = (
                "REQUEST",
                request.effective_question,
                request.operation_id,
                row.revision_digest,
            )
            links = (
                HistoryScopeLink(
                    thread_id=request.thread_id,
                    request_revision_digest=row.revision_digest,
                    association="PRODUCED_IN",
                ),
            )
        elif family == "CurrentResultManifest":
            result_fields = self._result(project, key, content, row.revision_digest)
            if result_fields is None:
                return None
            details = result_fields
        elif any(value.startswith("restored-from:") for value in revision.affected_refs):
            kind, title = "RESTORE", "연구 항목 복원"
        return HistoryItem.model_validate(
            {
                "item_id": f"SEMANTIC_REVISION:{revision.revision_id}",
                "kind": kind,
                "occurred_at": revision.created_at,
                "record_ref": ref,
                "operation_id": operation,
                "origin_request_revision_digest": origin,
                "scope_links": links,
                "association": "PRODUCED_IN" if links else "UNATTRIBUTED",
                "head_membership": self.membership(project, key, row.revision_digest),
                "title": title,
                "entity_type": revision.entity_type.value,
                "entity_id": revision.entity_id,
                "schema_family": family,
                "capability": capability,
                "completion": content.get("completion")
                if family == "CurrentResultManifest"
                else None,
                "phase": content.get("phase") if family == "CurrentResultManifest" else None,
                "currentness": currentness,
                **details,
            }
        ), content

    def _result(
        self, project: str, key: str, content: dict[str, object], digest: str
    ) -> dict[str, object] | None:
        from thoth.domain.research_codec import decode_current_result_manifest
        from thoth.domain.research_request import CurrentResultManifestV21
        from thoth.domain.resource_scope import ResourceUse

        try:
            manifest = decode_current_result_manifest(content)
        except ValueError as exc:
            raise RestoreError("HISTORY_SCHEMA_UNSUPPORTED") from exc
        ref = manifest.request_ref
        if ref.project_id != project or not self.access.may_read_revision(
            project, ref.revision_digest
        ):
            return None
        uses = tuple(ResourceUse.model_validate(use) for use in manifest.resource_uses)
        if any(
            use.project_id != project or not self.access.may_read(project, use.resource_ref)
            for use in uses
        ):
            return None
        request_rev = self.ledger.read_revision_by_digest(project, ref.revision_digest)
        request_snap = (
            None if request_rev is None else self.ledger.read_snapshot(request_rev.snapshot_id)
        )
        if request_rev is None or request_snap is None:
            raise RestoreError("HISTORY_REQUEST_UNAVAILABLE")
        request = ThreadRequestRevision.model_validate(request_snap.content)
        if (
            manifest.operation_id != request.operation_id
            or key != f"DECISION_OBJECT:result:{request.thread_id}"
            or ref.entity_type != request_rev.entity_type.value
            or ref.entity_id != request_rev.entity_id
            or ref.revision_id != request_rev.revision_id
        ):
            raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
        currentness = self.freshness.evaluate_result(
            project,
            manifest.research_basis if isinstance(manifest, CurrentResultManifestV21) else None,
            operation_id=manifest.operation_id,
            completion=manifest.completion,
        )
        if self.ledger.read_heads(project).get(key) != digest:
            currentness = currentness.model_copy(
                update={
                    "state": "REVIEW_REQUIRED"
                    if currentness.state == "CURRENT"
                    else currentness.state,
                    "reasons": tuple(dict.fromkeys((*currentness.reasons, "RESULT_SUPERSEDED"))),
                    "execution_eligible": False,
                }
            )
        return {
            "kind": "RESULT",
            "title": "연구 진행 중간 저장"
            if manifest.completion == "CHECKPOINT"
            else "당시 연구 답변",
            "operation_id": request.operation_id,
            "origin_request_revision_digest": ref.revision_digest,
            "association": "PRODUCED_IN",
            "scope_links": (
                HistoryScopeLink(
                    thread_id=request.thread_id,
                    request_revision_digest=ref.revision_digest,
                    association="PRODUCED_IN",
                ),
            ),
            "currentness": currentness,
        }

    def membership(self, project: str, key: str, digest: str) -> str:
        head = self.ledger.read_heads(project).get(key)
        if head == digest:
            return "CURRENT"
        frontier = [] if head is None else [head]
        seen: set[str] = set()
        while frontier and len(seen) < 2000:
            current = frontier.pop()
            if current == digest:
                return "ANCESTOR"
            if current in seen:
                continue
            seen.add(current)
            if not self.access.may_read_revision(project, current):
                return "UNKNOWN"
            revision = self.ledger.read_revision_by_digest(project, current)
            if revision is None:
                return "UNKNOWN"
            frontier.extend(revision.parent_revision_digests)
        return "UNKNOWN" if frontier or head is None else "BRANCH"
