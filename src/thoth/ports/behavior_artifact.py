from __future__ import annotations

from typing import Protocol

from thoth.domain.behavior_artifact import (
    BehaviorArtifact,
    BehaviorArtifactKind,
    BehaviorRegistryEntry,
)


class BehaviorArtifactStorePort(Protocol):
    def add(self, value: BehaviorArtifact) -> None: ...
    def read(self, project_id: str, artifact_id: str) -> BehaviorArtifact | None: ...
    def list(
        self, project_id: str, kind: BehaviorArtifactKind | None = None
    ) -> tuple[BehaviorArtifact, ...]: ...
    def read_registry(
        self, project_id: str, kind: BehaviorArtifactKind
    ) -> BehaviorRegistryEntry | None: ...
    def activate(self, artifact: BehaviorArtifact, entry: BehaviorRegistryEntry) -> None: ...
    def rollback(self, candidate: BehaviorArtifact, entry: BehaviorRegistryEntry) -> None: ...


class BehaviorArtifactCatalogPort(Protocol):
    def baseline_content(self, kind: BehaviorArtifactKind) -> dict[str, object]: ...
