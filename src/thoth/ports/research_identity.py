from __future__ import annotations

from typing import Protocol

from thoth.domain.enums import EntityType
from thoth.domain.research_identity import ResearchIdentity
from thoth.domain.revision import EntitySnapshot, SemanticRevision


class ResearchIdentityStorePort(Protocol):
    def add(self, identity: ResearchIdentity) -> None: ...

    def read(self, project_id: str, owner_revision: str) -> ResearchIdentity | None: ...

    def find_entity(self, entity_id: str, kind: EntityType) -> tuple[ResearchIdentity, ...]: ...

    def unindexed_revisions(self) -> tuple[tuple[SemanticRevision, EntitySnapshot], ...]: ...
