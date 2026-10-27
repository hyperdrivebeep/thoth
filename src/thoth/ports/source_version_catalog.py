"""Optional version-local catalog reads; scoped implementations authorize the source owner."""

from typing import Protocol, runtime_checkable

from thoth.domain.evidence import EvidenceSpan


@runtime_checkable
class SourceVersionCatalogPort(Protocol):
    def read_evidence_version(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> tuple[EvidenceSpan, ...]: ...
