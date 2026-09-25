from __future__ import annotations

from thoth.domain.artifact import StructuralDocument
from thoth.domain.base import DomainModel
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import Sha256, SourceVersionId
from thoth.domain.resource_scope import StagedResourceScope


class IngestionResult(DomainModel):
    document: StructuralDocument
    source_version_id: SourceVersionId
    evidence_candidates: tuple[EvidenceSpan, ...]
    object_digest: Sha256
    schema_version: str = "1.0.0"
    resource_scope: StagedResourceScope | None = None
