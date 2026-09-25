"""Profiles own typed shape/reference rules; registration stays outside the planner."""

from typing import Protocol

from thoth.domain.base import DomainModel
from thoth.domain.restore import RestoreReferenceBasis
from thoth.domain.revision import EntitySnapshot, SemanticRevision


class RestoreProfilePort(Protocol):
    @property
    def profile_id(self) -> str: ...
    @property
    def display_name(self) -> str: ...

    def decode(
        self, revision: SemanticRevision, snapshot: EntitySnapshot
    ) -> DomainModel | None: ...
    def object_id(self, record: DomainModel) -> str: ...
    def references(self, record: DomainModel) -> tuple[str, ...]: ...
    def evidence_refs(self, record: DomainModel) -> tuple[str, ...]: ...


class RestoreSourceResolverPort(Protocol):
    def resolve(
        self, project: str, target_digest: str, refs: tuple[str, ...]
    ) -> dict[str, tuple[str, str | None]]: ...


class RestoreReferenceResolverPort(Protocol):
    def resolve(self, target: SemanticRevision, record: DomainModel) -> RestoreReferenceBasis: ...


class RestoreProfileRegistryPort(Protocol):
    def resolve(
        self, revision: SemanticRevision, snapshot: EntitySnapshot
    ) -> tuple[RestoreProfilePort, DomainModel]: ...
