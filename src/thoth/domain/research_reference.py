"""Shared immutable revision identity, independent of any result payload version."""

from thoth.domain.base import DomainModel


class RevisionRef(DomainModel):
    project_id: str
    entity_type: str
    entity_id: str
    revision_id: str
    revision_digest: str
    schema_version: str = "2.0.0"
