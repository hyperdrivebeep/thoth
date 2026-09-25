"""A mutable span's current bytes cannot substitute for the target's historical source."""

from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.restore import RestoreError
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_history import ResearchHistoryReadPort
from thoth.ports.restore import RestoreSourceResolverPort


def content_origin(
    ledger: LedgerPort, revision: SemanticRevision, snapshot: EntitySnapshot
) -> SemanticRevision:
    seen: set[str] = set()
    current = revision
    while True:
        if current.revision_digest in seen or len(seen) >= 32:
            raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
        seen.add(current.revision_digest)
        refs = [
            ref.removeprefix("restored-from:")
            for ref in current.affected_refs
            if ref.startswith("restored-from:")
        ]
        if not refs:
            return current
        if len(refs) != 1:
            raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
        previous = ledger.read_revision_by_id(revision.project_id, refs[0])
        source = None if previous is None else ledger.read_snapshot(previous.snapshot_id)
        if (
            previous is None
            or source is None
            or previous.entity_type != revision.entity_type
            or previous.entity_id != revision.entity_id
            or source.content_digest != snapshot.content_digest
        ):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        current = previous


def historical_source_basis(
    ledger: LedgerPort,
    history: ResearchHistoryReadPort,
    project: str,
    target_digest: str,
    refs: tuple[str, ...],
) -> dict[str, tuple[str, str | None]]:
    if not refs:
        return {}
    rows = history.page(project, history.watermark(project), None, 2001)
    observed: dict[str, set[tuple[str, str | None]]] = {}
    for row in rows:
        if row.owner_kind != "SEMANTIC_REVISION":
            continue
        revision = ledger.read_revision_by_digest(project, row.revision_digest)
        snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None or snapshot.content.get("record_kind") != "CurrentResultManifest":
            continue
        raw = snapshot.content.get("research_basis")
        if raw is None:
            continue
        basis = ResearchResultBasis.model_validate(raw)
        if target_digest not in {ref.revision_digest for ref in basis.produced_refs}:
            continue
        for source in basis.source_basis:
            if source.span_id in refs:
                observed.setdefault(str(source.span_id), set()).add(
                    (source.source_version_id, source.text_sha256)
                )
    if any(len(values) != 1 for values in observed.values()):
        raise RestoreError("RESTORE_SOURCE_DRIFT")
    return {ref: next(iter(values)) for ref, values in observed.items()}


class ManifestRestoreSources:
    def __init__(self, ledger: LedgerPort, history: ResearchHistoryReadPort) -> None:
        self.ledger, self.history = ledger, history

    def resolve(
        self, project: str, target_digest: str, refs: tuple[str, ...]
    ) -> dict[str, tuple[str, str | None]]:
        return historical_source_basis(self.ledger, self.history, project, target_digest, refs)


class ImmutableSpanRestoreSources:
    def __init__(self, history: ResearchHistoryReadPort) -> None:
        self.history = history

    def resolve(
        self, project: str, target_digest: str, refs: tuple[str, ...]
    ) -> dict[str, tuple[str, str | None]]:
        del target_digest
        result: dict[str, tuple[str, str | None]] = {}
        for ref in refs:
            basis = self.history.read_immutable_span_basis(project, ref)
            if basis is not None:
                result[ref] = (basis.source_version_id, basis.text_sha256)
        return result


class RegisteredRestoreSources:
    def __init__(self, resolvers: tuple[RestoreSourceResolverPort, ...]) -> None:
        self.resolvers = resolvers

    def resolve(
        self, project: str, target_digest: str, refs: tuple[str, ...]
    ) -> dict[str, tuple[str, str | None]]:
        proof: dict[str, tuple[str, str | None]] = {}
        for resolver in self.resolvers:
            for ref, value in resolver.resolve(project, target_digest, refs).items():
                if ref not in refs or (ref in proof and proof[ref] != value):
                    raise RestoreError("RESTORE_SOURCE_DRIFT")
                proof[ref] = value
        if set(proof) != set(refs):
            raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
        return proof
