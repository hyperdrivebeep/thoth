from __future__ import annotations

from thoth.domain.behavior_artifact import BehaviorArtifact, BehaviorRegistryEntry
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.runtime import ClockPort


class ImprovementRunner:
    def __init__(self, *, store: BehaviorArtifactStorePort, clock: ClockPort) -> None:
        self._store = store
        self._clock = clock

    def promote(
        self, baseline: BehaviorArtifact, candidate: BehaviorArtifact
    ) -> BehaviorRegistryEntry:
        if not candidate.promotion_eligible or not candidate.auto_apply_allowed:
            raise ValueError("behavior artifact kind cannot be automatically promoted")
        current = self._store.read_registry(candidate.project_id, candidate.kind)
        entry = BehaviorRegistryEntry(
            project_id=candidate.project_id,
            kind=candidate.kind,
            active_artifact_id=candidate.artifact_id,
            active_digest=candidate.content_digest,
            baseline_digest=baseline.content_digest,
            revision=1 if current is None else current.revision + 1,
            updated_at=self._clock.now(),
        )
        self._store.activate(candidate, entry)
        return entry

    def rollback(
        self, baseline: BehaviorArtifact, candidate: BehaviorArtifact
    ) -> BehaviorRegistryEntry:
        current = self._store.read_registry(candidate.project_id, candidate.kind)
        entry = BehaviorRegistryEntry(
            project_id=candidate.project_id,
            kind=candidate.kind,
            active_artifact_id=baseline.artifact_id,
            active_digest=baseline.content_digest,
            baseline_digest=baseline.content_digest,
            revision=1 if current is None else current.revision + 1,
            updated_at=self._clock.now(),
        )
        self._store.rollback(candidate, entry)
        return entry
