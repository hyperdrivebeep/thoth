"""Many-to-many episode links from exact result manifests, never object-name guesses."""

from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.research_history import HistoryScopeLink
from thoth.domain.research_request import ThreadRequestRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_history import HistoryWatermark, ResearchHistoryReadPort
from thoth.ports.resource_scope import ResourceAccessPort


def result_scope_links(
    ledger: LedgerPort,
    reader: ResearchHistoryReadPort,
    access: ResourceAccessPort,
    project: str,
    watermark: HistoryWatermark,
) -> tuple[dict[str, tuple[HistoryScopeLink, ...]], bool]:
    rows = reader.page(project, watermark, None, 2001)
    links: dict[str, list[HistoryScopeLink]] = {}
    for row in rows[:2000]:
        if row.owner_kind != "SEMANTIC_REVISION" or not access.may_read_revision(
            project, row.revision_digest
        ):
            continue
        revision = ledger.read_revision_by_digest(project, row.revision_digest)
        snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None or snapshot.content.get("record_kind") != "CurrentResultManifest":
            continue
        raw_basis = snapshot.content.get("research_basis")
        if raw_basis is None:
            continue
        basis = ResearchResultBasis.model_validate(raw_basis)
        if basis.request_ref.project_id != project or not access.may_read_revision(
            project, basis.request_ref.revision_digest
        ):
            continue
        request_revision = ledger.read_revision_by_digest(
            project, basis.request_ref.revision_digest
        )
        request_snapshot = (
            None if request_revision is None else ledger.read_snapshot(request_revision.snapshot_id)
        )
        if request_snapshot is None:
            continue
        request = ThreadRequestRevision.model_validate(request_snapshot.content)
        if request.operation_id != snapshot.content.get("operation_id"):
            continue
        producers = {ref.revision_digest for ref in basis.produced_refs}
        for digest in {*producers, *basis.consumed_heads.values(), *basis.memory_revision_refs}:
            link = HistoryScopeLink(
                thread_id=request.thread_id,
                request_revision_digest=basis.request_ref.revision_digest,
                association="PRODUCED_IN" if digest in producers else "USED_BY",
            )
            values = links.setdefault(digest, [])
            if link not in values:
                values.append(link)
    return {key: tuple(value) for key, value in links.items()}, len(rows) > 2000
