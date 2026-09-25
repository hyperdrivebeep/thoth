from __future__ import annotations

from typing import Protocol

from thoth.domain.enums import ImpactStatus
from thoth.domain.relation import DependencyRelation


class DependencyGraphPort(Protocol):
    def read_relations(self, project_id: str) -> tuple[DependencyRelation, ...]: ...

    def add(self, relation: DependencyRelation) -> None: ...

    def downstream(self, project_id: str, source_ref: str) -> tuple[str, ...]: ...

    def read_states(self, project_id: str) -> dict[str, ImpactStatus]: ...

    def mark_current(
        self,
        project_id: str,
        entity_refs: tuple[str, ...],
        *,
        caused_by_revision: str,
        updated_at: str,
    ) -> None: ...
