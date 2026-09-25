"""History admission and caller identity are checked without creating records."""

from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.research_history import HistoryScope
from thoth.domain.research_request import ThreadRequestRevision
from thoth.domain.restore import RestoreError
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.thread import ThreadStorePort


def actor_scope_basis(project_id: str) -> dict[str, object]:
    actor = current_authenticated_actor()
    if actor is not None and actor.project_id != project_id:
        raise RestoreError("HISTORY_SCOPE_MISMATCH")
    return (
        actor.model_dump(mode="json")
        if actor is not None
        else {
            "project_id": project_id,
            "actor_id": "human:local-user",
            "mode": "LOCAL",
        }
    )


def actor_scope_digest(project_id: str) -> str:
    return domain_digest(
        "HISTORY_ACTOR_SCOPE", "1.0.0", canonical_payload(actor_scope_basis(project_id))
    )


class HistoryScopeValidator:
    def __init__(
        self,
        ledger: LedgerPort,
        access: ResourceAccessPort,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
    ) -> None:
        self.ledger, self.access, self.projects, self.threads = ledger, access, projects, threads

    def require(self, project_id: str, scope: HistoryScope) -> None:
        if project_id != scope.project_id:
            raise RestoreError("HISTORY_SCOPE_MISMATCH")
        actor_scope_basis(project_id)
        self.access.require_reads(project_id, ())
        if self.projects.read(project_id) is None:
            raise RestoreError("HISTORY_SCOPE_UNAVAILABLE")
        if scope.thread_id is not None:
            thread = self.threads.read(scope.thread_id)
            if thread is None or thread.project_id != project_id:
                raise RestoreError("HISTORY_SCOPE_UNAVAILABLE")
            from thoth.domain.auth import authenticated_data_scope_allows

            if not authenticated_data_scope_allows(thread.scope):
                raise RestoreError("HISTORY_SCOPE_UNAVAILABLE")
        if scope.request_revision_digest is not None:
            request = self.request(project_id, scope.request_revision_digest)
            if scope.thread_id is not None and request.thread_id != scope.thread_id:
                raise RestoreError("HISTORY_SCOPE_MISMATCH")
        if scope.entity_ref is not None:
            digest = self.ledger.read_heads(project_id).get(scope.entity_ref)
            if digest is None:
                raise RestoreError("HISTORY_SCOPE_UNAVAILABLE")
            self.access.require_revision(project_id, digest)

    def request(self, project_id: str, digest: str) -> ThreadRequestRevision:
        revision = self.ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else self.ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            raise RestoreError("HISTORY_REQUEST_UNAVAILABLE")
        request = ThreadRequestRevision.model_validate(snapshot.content)
        if (
            request.project_id != project_id
            or revision is None
            or (
                revision.entity_type.value != "THREAD"
                or revision.entity_id != f"request:{request.thread_id}"
            )
        ):
            raise RestoreError("HISTORY_SCOPE_MISMATCH")
        self.access.require_revision(project_id, request.authored_text_ref.revision_digest)
        return request
